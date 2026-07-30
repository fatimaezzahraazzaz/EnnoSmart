from PIL import Image
from surya.foundation import FoundationPredictor
from surya.recognition import RecognitionPredictor
from surya.detection import DetectionPredictor

foundation = FoundationPredictor()
recognizer = RecognitionPredictor(foundation)
detector = DetectionPredictor()

image = Image.open(r"C:\EnnoSmart\data\raw_documents\Archi_V1.drawio (2).png").convert("RGB")

predictions = recognizer([image], det_predictor=detector)

for line in predictions[0].text_lines:
    print(line.text, getattr(line, "confidence", None))
