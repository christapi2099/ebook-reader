import numpy as np


class OCREngine:
    def __init__(self):
        try:
            import easyocr
            self.reader = easyocr.Reader(['en'], gpu=False)
        except ImportError:
            self.reader = None

    def is_available(self) -> bool:
        return self.reader is not None

    def process_page(self, page) -> list[dict]:
        if self.reader is None:
            return []

        pixmap = page.get_pixmap(dpi=150)
        samples = pixmap.samples
        width = pixmap.width
        height = pixmap.height
        n = pixmap.n

        array = np.frombuffer(samples, dtype=np.uint8).reshape((height, width, n))
        if n == 4:
            array = array[:, :, :3]

        results = self.reader.readtext(array)
        ocr_results = []
        for result in results:
            bbox, text, confidence = result
            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]
            ocr_results.append({
                'text': text,
                'x0': float(min(xs)),
                'y0': float(min(ys)),
                'x1': float(max(xs)),
                'y1': float(max(ys)),
                'confidence': float(confidence),
            })
        return ocr_results
