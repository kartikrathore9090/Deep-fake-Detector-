import json
import os
import uuid
import base64
import logging
import io
from deepfake_detector import highlight_face
from flask import Flask, render_template, request, redirect, url_for, jsonify
from PIL import Image
import torch
from ai_models import detect_deepfake, detect_emotion, estimate_age, full_analysis, get_model_status, get_deepfake_model, get_emotion_model, get_age_model
from kafka_service import KafkaService
from kaggle_integration import get_datasets, download_dataset, benchmark_model, is_kaggle_configured
from history_service import add_scan_entry, get_history, get_session_stats

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Flask App ────────────────────────────────────────────────
app = Flask(__name__)

# ── Pre-load deepfake model (others load on demand) ──────────
deepfake_m = get_deepfake_model()

# ── Initialize Kafka ─────────────────────────────────────────
kafka_broker = os.environ.get("KAFKA_BROKER", "localhost:9092")
kafka_enabled = os.environ.get("KAFKA_ENABLED", "true").lower() == "true"
kafka = KafkaService(broker=kafka_broker, enabled=kafka_enabled)
logger.info("🔌 Kafka status: %s", kafka.status)


# ══════════════════════════════════════════════════════════════
#  HOME — Deepfake Detection
# ══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    confidence = None
    output_image = None
    filename = None

    if request.method == "POST":
        file = request.files["image"]
        image = Image.open(file).convert("RGB")

        upload_folder = "static"
        os.makedirs(upload_folder, exist_ok=True)

        filename = f"{uuid.uuid4()}.jpg"
        image_path = os.path.join(upload_folder, filename)
        image.save(image_path)

        # Run deepfake detection
        detection = detect_deepfake(image)
        result = detection["result"]
        confidence = detection["confidence"]

        # Highlight suspicious region
        output_image = highlight_face(image_path)

        # Publish to Kafka
        kafka.publish_detection({
            "filename": filename,
            "result": result,
            "confidence": confidence,
        })

        # Save to history
        add_scan_entry({
            "type": "deepfake",
            "filename": filename,
            "result": result,
            "confidence": confidence,
        })

    return render_template(
        "index.html",
        result=result,
        confidence=confidence,
        output_image=output_image,
        uploaded_image=filename
    )


# ══════════════════════════════════════════════════════════════
#  FEEDBACK
# ══════════════════════════════════════════════════════════════

@app.route("/feedback", methods=["POST"])
def feedback():
    name = request.form.get("name")
    rating = request.form.get("rating")
    message = request.form.get("message")
    prediction_correct = request.form.get("prediction_correct")

    data = {
        "name": name,
        "rating": rating,
        "message": message,
        "was_prediction_correct": prediction_correct,
    }

    file_path = "feedback.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                feedback_data = json.load(f)
            except json.JSONDecodeError:
                feedback_data = []
    else:
        feedback_data = []

    feedback_data.append(data)
    with open(file_path, "w") as f:
        json.dump(feedback_data, f, indent=4)

    kafka.publish_feedback(data)

    return redirect(url_for("index"))


# ══════════════════════════════════════════════════════════════
#  HISTORY SCAN
# ══════════════════════════════════════════════════════════════

@app.route("/history")
def history_page():
    history = get_history(limit=100)
    stats = get_session_stats()
    return render_template("history.html", history=history, stats=stats)


# ══════════════════════════════════════════════════════════════
#  EMOTION DETECTOR
# ══════════════════════════════════════════════════════════════

@app.route("/emotion", methods=["GET", "POST"])
def emotion_page():
    result = None

    if request.method == "POST":
        # Handle both file upload and base64 (webcam)
        if "image" in request.files and request.files["image"].filename:
            file = request.files["image"]
            image = Image.open(file).convert("RGB")
        elif request.is_json and request.json.get("image_data"):
            img_data = request.json["image_data"].split(",")[1]
            image = Image.open(io.BytesIO(base64.b64decode(img_data))).convert("RGB")
        else:
            return render_template("emotion.html", result=None)

        result = detect_emotion(image)

        # Save to history
        add_scan_entry({
            "type": "emotion",
            "primary_emotion": result.get("primary_emotion"),
            "confidence": result.get("confidence", 0) / 100,
            "deception_score": result.get("deception_score", 0),
        })

        if request.is_json:
            return jsonify(result)

    return render_template("emotion.html", result=result)


@app.route("/api/emotion", methods=["POST"])
def api_emotion():
    """API endpoint for webcam emotion detection."""
    data = request.get_json()
    if not data or not data.get("image_data"):
        return jsonify({"error": "No image data"}), 400

    img_data = data["image_data"].split(",")[1]
    image = Image.open(io.BytesIO(base64.b64decode(img_data))).convert("RGB")
    result = detect_emotion(image)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  AGE ESTIMATION
# ══════════════════════════════════════════════════════════════

@app.route("/age", methods=["GET", "POST"])
def age_page():
    result = None

    if request.method == "POST":
        if "image" in request.files and request.files["image"].filename:
            file = request.files["image"]
            image = Image.open(file).convert("RGB")
        else:
            return render_template("age.html", result=None)

        result = estimate_age(image)

        add_scan_entry({
            "type": "age",
            "estimated_age": result.get("estimated_age"),
            "age_group": result.get("age_group"),
            "confidence": result.get("confidence", 0) / 100,
            "is_minor": result.get("is_minor"),
        })

    return render_template("age.html", result=result)


@app.route("/api/age", methods=["POST"])
def api_age():
    """API endpoint for webcam age estimation."""
    data = request.get_json()
    if not data or not data.get("image_data"):
        return jsonify({"error": "No image data"}), 400

    img_data = data["image_data"].split(",")[1]
    image = Image.open(io.BytesIO(base64.b64decode(img_data))).convert("RGB")
    result = estimate_age(image)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  VIDEO CALL ANALYSIS
# ══════════════════════════════════════════════════════════════

@app.route("/video-analysis")
def video_analysis_page():
    return render_template("video_analysis.html")


@app.route("/api/analyze-frame", methods=["POST"])
def api_analyze_frame():
    """Analyze a single video frame with all models."""
    data = request.get_json()
    if not data or not data.get("image_data"):
        return jsonify({"error": "No image data"}), 400

    img_data = data["image_data"].split(",")[1]
    image = Image.open(io.BytesIO(base64.b64decode(img_data))).convert("RGB")

    result = full_analysis(image)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  DATASETS (Kaggle)
# ══════════════════════════════════════════════════════════════

@app.route("/datasets")
def datasets_page():
    datasets = get_datasets()
    kaggle_ready = is_kaggle_configured()
    return render_template("datasets.html", datasets=datasets, kaggle_ready=kaggle_ready)


@app.route("/datasets/download", methods=["POST"])
def datasets_download():
    slug = request.form.get("slug")
    if not slug:
        return jsonify({"success": False, "message": "No dataset specified"})
    m = get_deepfake_model()
    result = download_dataset(slug)
    return jsonify(result)


@app.route("/datasets/benchmark", methods=["POST"])
def datasets_benchmark():
    slug = request.form.get("slug")
    if not slug:
        return jsonify({"success": False, "message": "No dataset specified"})

    folder_name = slug.replace("/", "_")
    dataset_dir = os.path.join("datasets", folder_name)
    if not os.path.exists(dataset_dir):
        return jsonify({"success": False, "message": "Dataset not downloaded yet"})

    m = get_deepfake_model()
    result = benchmark_model(dataset_dir, m["processor"], m["model"])
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  EVENTS (Kafka)
# ══════════════════════════════════════════════════════════════

@app.route("/events")
def events_page():
    events = kafka.get_recent_events(limit=50)
    stats = kafka.get_stats()
    return render_template("events.html", events=events, stats=stats, kafka_status=kafka.status)


@app.route("/api/events")
def api_events():
    event_type = request.args.get("type")
    limit = int(request.args.get("limit", 50))
    events = kafka.get_recent_events(limit=limit, event_type=event_type)
    stats = kafka.get_stats()
    return jsonify({"events": events, "stats": stats})


# ══════════════════════════════════════════════════════════════
#  API: Model Status
# ══════════════════════════════════════════════════════════════

@app.route("/api/model-status")
def api_model_status():
    return jsonify(get_model_status())


# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=True)