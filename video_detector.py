import cv2
import torch
import numpy as np
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification

# Load model once at the top level
MODEL_NAME = "prithivMLmods/Deep-Fake-Detector-Model"
processor = AutoImageProcessor.from_pretrained(MODEL_NAME)
model = AutoModelForImageClassification.from_pretrained(MODEL_NAME)

def highlight_face(image_path):
    img = cv2.imread(image_path)
    if img is None:
        return None

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        # Draw a red rectangle around faces
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 3)

    output_path = image_path.replace(".jpg", "_analyzed.jpg")
    cv2.imwrite(output_path, img)
    return output_path

def extract_frames(video_path, frame_rate=10):
    cap = cv2.VideoCapture(video_path)
    frames = []
    count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        if count % frame_rate == 0:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(frame))
        count += 1
    cap.release()
    return frames

def analyze_video(video_path):
    frames = extract_frames(video_path)
    if not frames:
        return {"result": "ERROR", "confidence": 0, "fake_frames": 0, "total_frames": 0}

    fake_count = 0
    real_count = 0

    for frame in frames:
        inputs = processor(images=frame, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        
        probs = torch.softmax(outputs.logits, dim=1)
        pred = torch.argmax(probs).item()

        if pred == 0:
            real_count += 1
        else:
            fake_count += 1

    total = real_count + fake_count
    confidence = (fake_count / total) * 100 if total > 0 else 0
    result = "DEEPFAKE VIDEO" if fake_count > real_count else "REAL VIDEO"

    return {
        "result": result,
        "fake_frames": fake_count,
        "total_frames": total,
        "confidence": round(confidence, 2)
    }