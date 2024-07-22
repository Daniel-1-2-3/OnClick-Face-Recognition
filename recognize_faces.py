import cv2 
import sqlite3
import numpy as np
import time
import pickle
import copy
from PIL import Image
import torch
from torchvision import transforms
from Optimize_FaceNet.quantize_torch_model.fuse_modules import Fusion
from facenet_pytorch import InceptionResnetV1
import torch.nn as nn

"""
    To optimize performance of model in recognizing faces (reducing false positives and false negative), adjust 2 values:
        1.  Threshold value of what distance between the 2 embedding vectors qualifies as a match: 0.35 - 0.45
        2.  Size of img (too large -> too much details confuse model, too small -> not enough detail to analyze): 500x 500
    In addition, for more accuracy, take average of distance generated by all 10 comparisons instead of taking 
    only the lowest one.
    
"""
class RecognizeFaces:
    def __init__(self):
        self.con = sqlite3.connect('StoredFaces.db')
        self.cur = self.con.cursor()

        #model to calculate with imbeddings (convert cropped faces to small dimensional vectors that represent the key features, like flattened feature maps), compare distance between two vectors, shorter the distance the more likely for a match
        self.model = InceptionResnetV1(pretrained='vggface2').eval()
        self.fusion = Fusion()
        self.model = self.fusion.fuse()
        self.model = nn.Sequential(torch.quantization.QuantStub(), 
                            self.model, 
                            torch.quantization.DeQuantStub()) 
        self.model.qconfig = torch.quantization.get_default_qconfig('fbgemm')
        torch.quantization.prepare(self.model, inplace=True)
        torch.quantization.convert(self.model, inplace=True)
        self.model.load_state_dict(torch.load('Optimize_FaceNet\\model_versions\\quant_torch_model.pth'), strict=False)

        self.cur.execute('''SELECT * FROM embeddings''')
        self.rows = self.cur.fetchall() #list of the all the rows in the database, info on every person
        self.con.close()
        
    def get_embedding(self, frame):
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Resize((500, 500)),
        ])
        
        img_tensor = transform(frame).unsqueeze(0)
        with torch.no_grad():
            embedding = self.model(img_tensor)
        return embedding 
    
    def analyze_faces(self, face):
        start_time = time.time()
        face_emb = self.get_embedding(face) #get embeddings for that face
        end_time = time.time()
            
        matches = []
        for row in self.rows:
            name = row[0] #column one of the row is always the name of the person
            ref_embeddings = [] #list in which we load into the 10 reference embeddings of the person's face in the columns 1-10 of that row
            for i in range (1, len(row)):
                if row[i] is not None:
                    emb = pickle.loads(row[i]) #deserializes the embedding
                    ref_embeddings.append(emb)
            
            emb = sum(ref_embeddings)/len(ref_embeddings)
            distance = torch.dist(emb, face_emb, p=2).item() #calcuate Euclidean distance between the reference face embed and embed of face in the picture, closer distance = closer match
        
            #distances usually range from 0.20 to 1.10, threshold for face to qualify as a match is 0.25
            print(name, distance)
            if distance < 0.35: #if distance between the 2 vectors is less than 0.50, it is a match. Threshold value taken from trial and error
                matches.append((name, distance)) 

        if len(matches) == 0:
            return None, None
            
        #choose the highest match, choose shortest distance
        lowest_distance = matches[0][1]
        highest_match = (matches[0][0], lowest_distance)
        for i, (match, distance) in enumerate(matches):
            if i!=0 and distance < lowest_distance:
                lowest_distance = distance
                highest_match = (match, distance) #calculate closest match
                    
        print('Highest match', highest_match)
        print('Speed', end_time-start_time)
            
        return highest_match

    def recognize(self):
        cap = cv2.VideoCapture(0)
        haar_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
        open_windows = []
        while True:
            ret, frame = cap.read()
            height, width, _ = frame.shape
            frame = cv2.flip(frame, 1)
            
            if cv2.waitKey(1) & 0xFF == ord(' '):
                #crop the frame using MTCNN to leave just the faces
                faces = haar_cascade.detectMultiScale(
                        frame, scaleFactor=1.05, minNeighbors=3, minSize=(100,100)
                    )
                
                #close face{i} windows that are currently open
                if len(open_windows)>0:
                    for window_name in open_windows:
                        if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) >= 1: #if window still open, wasn't close manually 
                            cv2.destroyWindow(window_name)
                open_windows = []
                 
                frame_copy = copy.deepcopy(frame)
                #iterate over the faces and process
                for i, (x, y, w, h) in enumerate(faces):
                    cropped_img = frame_copy[y : y+h, x : x+w]
                    highest_match, distance = self.analyze_faces(cropped_img)
                    blank_frame = np.ones((height, width, 3), dtype=np.uint8) * 255
                    frame = blank_frame
                
                    if highest_match is not None:
                        open_windows.append(f'face{i}')
                        percentage_match = round(100 - max((distance - 0.2), 0)/(0.7 - 0.2) * 100, 2) #max means that if distance - 0.2 < 0, will return the larger of the 2 numbers (returns 0)
                        cv2.putText(frame_copy, f'{highest_match} {percentage_match}%', (x, y-20), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
                        cv2.rectangle(frame_copy, (x, y), (x + w, y + h), (0, 200, 0), 2)
                        cv2.imshow(f'face{i}', frame_copy)
                        
            cv2.putText(frame, f'Click space to analyze face, q to exit camera', (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 2)
            cv2.imshow('Webcam',frame)
            
            if cv2.waitKey(1) == ord('q'):
                break
            
        cap.release()
        cv2.destroyAllWindows()
recognize_faces = RecognizeFaces()
recognize_faces.recognize()
                        



