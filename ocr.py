from collections import defaultdict, deque
from pathlib import Path
import pytesseract
from PIL import Image, ImageDraw


def ocr(
    image_path: str = "./img/screenshot.png",
    *,
    mode: str = "chunk",  # "line" or "chunk"
    visualize: bool = False,
    visualize_path: str = "./img/ocr_bboxes.png",
    x_thresh: float = 20,  # horizontal gap for merging in chunk mode
    y_thresh: float = 8,   # vertical gap for merging in chunk mode
    group_y_thresh: float = 35  # vertical gap to separate different question groups
):
    """
    Perform OCR on an image.

    Returns:
        A list of dictionaries for each chunk:
        {
            "text": "chunk text",
            "bbox": (x, y, w, h),
            "group_id": 1
        }
    """
    image = Image.open(image_path)
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    results = []

    # ---- LINE MODE ----
    if mode == "line":
        lines = defaultdict(list)
        for i, text in enumerate(data["text"]):
            if text.strip():
                line_num = data["line_num"][i]
                lines[line_num].append(
                    (
                        text,
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                    )
                )
        for line_num, words in lines.items():
            full_text = " ".join([word[0] for word in words])
            x = min([word[1] for word in words])
            y = min([word[2] for word in words])
            w = max([word[1] + word[3] for word in words]) - x
            h = max([word[2] + word[4] for word in words]) - y
            results.append({"text": full_text, "bbox": (x, y, w, h)})

    # ---- CHUNK MODE ----
    elif mode == "chunk":
        boxes = []
        for i, text in enumerate(data["text"]):
            if text.strip():
                boxes.append({
                    "text": text,
                    "x": data["left"][i],
                    "y": data["top"][i],
                    "w": data["width"][i],
                    "h": data["height"][i],
                    "used": False
                })

        # compute gaps between boxes
        def box_distance(box1, box2):
            x_gap = max(box2["x"] - (box1["x"] + box1["w"]),
                        box1["x"] - (box2["x"] + box2["w"]),
                        0)
            y_gap = max(box2["y"] - (box1["y"] + box1["h"]),
                        box1["y"] - (box2["y"] + box2["h"]),
                        0)
            return x_gap, y_gap

        def is_close(box1, box2):
            x_gap, y_gap = box_distance(box1, box2)
            return x_gap <= x_thresh and y_gap <= y_thresh

        # merge boxes into chunks
        for i, box in enumerate(boxes):
            if box["used"]:
                continue
            chunk_text = box["text"]
            x1, y1 = box["x"], box["y"]
            x2, y2 = box["x"] + box["w"], box["y"] + box["h"]
            box["used"] = True

            queue = deque([box])
            while queue:
                current = queue.popleft()
                for other in boxes:
                    if not other["used"] and is_close(current, other):
                        x1 = min(x1, other["x"])
                        y1 = min(y1, other["y"])
                        x2 = max(x2, other["x"] + other["w"])
                        y2 = max(y2, other["y"] + other["h"])
                        chunk_text += " " + other["text"]
                        other["used"] = True
                        queue.append(other)

            results.append({"text": chunk_text, "bbox": (x1, y1, x2 - x1, y2 - y1)})

    else:
        raise ValueError("mode must be 'line' or 'chunk'")

    # ---- Assign group IDs ----
    results.sort(key=lambda r: r["bbox"][1])  # sort by y (top)
    group_id = 1
    last_y = None
    for r in results:
        y = r["bbox"][1]
        if last_y is None or (y - last_y) > group_y_thresh:
            group_id += 1
        r["group_id"] = group_id
        last_y = y

    # ---- Visualization ----
    if visualize:
        annotated = image.convert("RGB").copy()
        draw = ImageDraw.Draw(annotated)
        for r in results:
            x, y, w, h = r["bbox"]
            draw.rectangle([x, y, x + w, y + h], outline="lime", width=2)
            draw.text((x, max(0, y - 12)), f"[G{r['group_id']}] {r['text'][:30]}", fill="lime")
        out_path = Path(visualize_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        annotated.save(out_path)
        print(f"Saved OCR bbox visualization to: {out_path}")

    # ---- Print results ----
    for r in results:
        print(f"G{r['group_id']}: {r['text']} //// \nBBOX: {r['bbox']} ////\n")

    return results


def main() -> None:
    ocr(image_path="./img/test_img.jpg", mode="chunk", visualize=True)


if __name__ == "__main__":
    main()
