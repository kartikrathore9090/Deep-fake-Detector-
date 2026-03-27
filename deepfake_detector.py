import cv2
import numpy as np

def highlight_face(image_path):

    img = cv2.imread(image_path)

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray,1.3,5)

    for (x,y,w,h) in faces:
        cv2.rectangle(img,(x,y),(x+w,y+h),(0,0,255),3)

    output_path = "static/result.jpg"
    cv2.imwrite(output_path,img)

    return output_path


