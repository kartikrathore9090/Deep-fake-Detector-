"""
AI Models — Centralized model loading for TruthLens AI.

Loads and provides inference functions for:
- Deepfake Detection (prithivMLmods/Deep-Fake-Detector-Model)
- Emotion Detection (trpakov/vit-face-expression)
- Age Estimation (nateraw/vit-age-classifier)
"""

import logging
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification, ViTImageProcessor, ViTForImageClassification

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  MODEL REGISTRY
# ══════════════════════════════════════════════════════════════

_models = {}


def _load_model(key, model_name, processor_class=AutoImageProcessor, model_class=AutoModelForImageClassification):
    """Load a model + processor pair, caching it."""
    if key not in _models:
        logger.info("🔄 Loading model: %s", model_name)
        try:
            proc = processor_class.from_pretrained(model_name)
            mdl = model_class.from_pretrained(model_name)
            mdl.eval()
            _models[key] = {"processor": proc, "model": mdl, "loaded": True}
            logger.info("✅ Model loaded: %s", model_name)
        except Exception as e:
            logger.error("❌ Failed to load model %s: %s", model_name, e)
            _models[key] = {"processor": None, "model": None, "loaded": False, "error": str(e)}
    return _models[key]


# ── Deepfake Detection ───────────────────────────────────────

def get_deepfake_model():
    return _load_model("deepfake", "prithivMLmods/Deep-Fake-Detector-Model")


def detect_deepfake(image: Image.Image) -> dict:
    """
    Analyze an image for deepfake manipulation.
    Returns: {"result": str, "confidence": float, "probabilities": dict}
    """
    m = get_deepfake_model()
    if not m["loaded"]:
        return {"result": "ERROR", "confidence": 0, "error": m.get("error")}

    inputs = m["processor"](images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = m["model"](**inputs)

    probs = torch.softmax(outputs.logits, dim=1)
    predicted = torch.argmax(probs).item()
    confidence = probs[0][predicted].item()

    result = "REAL IMAGE" if predicted == 0 else "DEEPFAKE IMAGE"

    return {
        "result": result,
        "confidence": confidence,
        "probabilities": {
            "real": probs[0][0].item(),
            "fake": probs[0][1].item(),
        }
    }


# ── Emotion Detection ────────────────────────────────────────

EMOTION_LABELS = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
EMOTION_EMOJIS = {
    "angry": "😠", "disgust": "🤢", "fear": "😨",
    "happy": "😊", "sad": "😢", "surprise": "😮", "neutral": "😐"
}
EMOTION_COLORS = {
    "angry": "#ef4444", "disgust": "#a855f7", "fear": "#f97316",
    "happy": "#22c55e", "sad": "#3b82f6", "surprise": "#eab308", "neutral": "#94a3b8"
}


def get_emotion_model():
    return _load_model("emotion", "trpakov/vit-face-expression")


def detect_emotion(image: Image.Image) -> dict:
    """
    Detect emotions from a face image.
    Returns: {"primary_emotion": str, "confidence": float, "all_emotions": list}
    """
    m = get_emotion_model()
    if not m["loaded"]:
        return {"primary_emotion": "unknown", "confidence": 0, "error": m.get("error")}

    inputs = m["processor"](images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = m["model"](**inputs)

    probs = torch.softmax(outputs.logits, dim=1)[0]

    all_emotions = []
    for i, label in enumerate(EMOTION_LABELS):
        all_emotions.append({
            "label": label,
            "emoji": EMOTION_EMOJIS.get(label, ""),
            "color": EMOTION_COLORS.get(label, "#94a3b8"),
            "score": round(probs[i].item() * 100, 2),
        })

    # Sort by score descending
    all_emotions.sort(key=lambda x: x["score"], reverse=True)
    primary = all_emotions[0]

    # Deception cue analysis (heuristic based on emotion mix)
    deception_score = 0
    fear_score = next((e["score"] for e in all_emotions if e["label"] == "fear"), 0)
    surprise_score = next((e["score"] for e in all_emotions if e["label"] == "surprise"), 0)
    neutral_score = next((e["score"] for e in all_emotions if e["label"] == "neutral"), 0)

    # High fear + surprise with low neutral can indicate deception
    if fear_score > 15 and surprise_score > 10:
        deception_score = min(round((fear_score + surprise_score) / 2, 1), 95)
    elif fear_score > 20:
        deception_score = min(round(fear_score * 0.8, 1), 90)

    return {
        "primary_emotion": primary["label"],
        "primary_emoji": primary["emoji"],
        "primary_color": primary["color"],
        "confidence": primary["score"],
        "all_emotions": all_emotions,
        "deception_score": deception_score,
    }


# ── Age Estimation ────────────────────────────────────────────

AGE_GROUPS = {
    "child": {"range": "0-12", "icon": "fa-child", "color": "#06d6a0"},
    "teenager": {"range": "13-19", "icon": "fa-user", "color": "#3b82f6"},
    "young_adult": {"range": "20-35", "icon": "fa-user-graduate", "color": "#8b5cf6"},
    "adult": {"range": "36-55", "icon": "fa-user-tie", "color": "#f59e0b"},
    "senior": {"range": "56+", "icon": "fa-user-clock", "color": "#ef4444"},
}


def get_age_model():
    return _load_model("age", "nateraw/vit-age-classifier")


def estimate_age(image: Image.Image) -> dict:
    """
    Estimate age from a face image.
    Returns: {"estimated_age": str, "age_group": str, "confidence": float, "all_ages": list}
    """
    m = get_age_model()
    if not m["loaded"]:
        return {"estimated_age": "unknown", "confidence": 0, "error": m.get("error")}

    inputs = m["processor"](images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = m["model"](**inputs)

    probs = torch.softmax(outputs.logits, dim=1)[0]
    num_classes = len(probs)

    # The model uses age-range labels (e.g. "0-2", "3-9", "20-29", etc.)
    # Map each class to the midpoint of its range for weighted average
    id2label = m["model"].config.id2label
    midpoints = []
    for i in range(num_classes):
        label = id2label.get(i, str(i)) if id2label else str(i)
        if "-" in str(label):
            parts = str(label).split("-")
            midpoints.append((float(parts[0]) + float(parts[1])) / 2.0)
        else:
            try:
                midpoints.append(float(label))
            except ValueError:
                midpoints.append(float(i))

    ages = torch.tensor(midpoints, dtype=torch.float32)
    weighted_age = (probs * ages).sum().item()
    estimated_age = max(0, round(weighted_age))

    # Get confidence (peak probability region)
    top_prob = probs.max().item()

    # Determine age group
    if estimated_age <= 12:
        age_group = "child"
    elif estimated_age <= 19:
        age_group = "teenager"
    elif estimated_age <= 35:
        age_group = "young_adult"
    elif estimated_age <= 55:
        age_group = "adult"
    else:
        age_group = "senior"

    group_info = AGE_GROUPS.get(age_group, {})

    # Top age predictions
    top_indices = torch.topk(probs, min(5, num_classes)).indices.tolist()
    top_ages = [{"age": idx, "probability": round(probs[idx].item() * 100, 2)} for idx in top_indices]

    # Is minor check
    is_minor = estimated_age < 18

    return {
        "estimated_age": estimated_age,
        "age_group": age_group,
        "age_range": group_info.get("range", "Unknown"),
        "group_icon": group_info.get("icon", "fa-user"),
        "group_color": group_info.get("color", "#94a3b8"),
        "confidence": round(top_prob * 100, 2),
        "is_minor": is_minor,
        "top_ages": top_ages,
    }


# ── Combined Analysis (for Video Call) ───────────────────────

def full_analysis(image: Image.Image) -> dict:
    """
    Run all three models on a single image.
    Returns combined results from deepfake, emotion, and age models.
    """
    deepfake_result = detect_deepfake(image)
    emotion_result = detect_emotion(image)
    age_result = estimate_age(image)

    # Credibility score (composite)
    credibility = 100
    if deepfake_result.get("result") == "DEEPFAKE IMAGE":
        credibility -= 50
    if emotion_result.get("deception_score", 0) > 30:
        credibility -= emotion_result["deception_score"] * 0.3

    credibility = max(0, min(100, round(credibility, 1)))

    return {
        "deepfake": deepfake_result,
        "emotion": emotion_result,
        "age": age_result,
        "credibility_score": credibility,
    }


def get_model_status() -> dict:
    """Return loading status of all models."""
    return {
        "deepfake": _models.get("deepfake", {}).get("loaded", False),
        "emotion": _models.get("emotion", {}).get("loaded", False),
        "age": _models.get("age", {}).get("loaded", False),
    }