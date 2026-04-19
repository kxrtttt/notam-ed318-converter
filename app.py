"""
NOTAM → ED-318 GeoJSON Converter
Flask web application serving the converter UI.

Run:
    pip install flask pandas xlrd openpyxl
    python app.py

Then open http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory, render_template
import json
import os
import tempfile
from pathlib import Path
from converter import parse_excel, notam_to_ed318_feature, build_geojson
from datetime import datetime, timezone

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB upload limit

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    """Accept an Excel file and return parsed NOTAM list."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    suffix = Path(f.filename).suffix.lower()
    if suffix not in (".xls", ".xlsx"):
        return jsonify({"error": "Only .xls or .xlsx files are supported"}), 400

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        notams = parse_excel(tmp_path)
    except Exception as e:
        return jsonify({"error": f"Failed to parse file: {str(e)}"}), 422
    finally:
        os.unlink(tmp_path)

    return jsonify({"notams": notams, "total": len(notams)})


@app.route("/api/preview/<notam_id>", methods=["POST"])
def preview(notam_id):
    """Convert a single NOTAM row (sent in body) and return its GeoJSON."""
    row = request.get_json()
    if not row:
        return jsonify({"error": "No NOTAM data provided"}), 400
    try:
        feature = notam_to_ed318_feature(row)
        gj = build_geojson(feature, row["notam_num"], row.get("location", ""))
        return jsonify(gj)
    except Exception as e:
        return jsonify({"error": str(e)}), 422


@app.route("/api/download", methods=["POST"])
def download():
    """Convert a single NOTAM and return the GeoJSON file for download."""
    row = request.get_json()
    if not row:
        return jsonify({"error": "No NOTAM data provided"}), 400
    try:
        feature = notam_to_ed318_feature(row)
        gj = build_geojson(feature, row["notam_num"], row.get("location", ""))
        safe = row["notam_num"].replace("/", "_")
        out_path = OUTPUT_DIR / f"{safe}.geojson"
        with open(out_path, "w") as fp:
            json.dump(gj, fp, indent=2)
        return send_from_directory(
            OUTPUT_DIR.resolve(),
            f"{safe}.geojson",
            as_attachment=True,
            mimetype="application/geo+json",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 422


if __name__ == "__main__":
    app.run(debug=True, port=5000)
