"""
Data importer — scans data/imports/ and imports any recognised files.

Supported files:
  - MacroFactor export  (.xlsx — multi-sheet Excel file)
  - Strong export       (.csv  — comma-delimited workout log)
  - Apple Health export (.xml  — export.xml from iPhone Health app)

Usage:
  1. Drop your export files into the  data/imports/  folder.
  2. Double-click  import_data.py  or run:  python import_data.py
"""
import sys
import csv
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.database import initialize

IMPORTS_DIR = Path(__file__).parent / "data" / "imports"


def sniff_csv_type(filepath: Path) -> str | None:
    """Inspect CSV headers to determine if it's a Strong export."""
    try:
        with open(filepath, encoding="utf-8-sig", newline="") as f:
            sample = f.read(2000)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
        headers = {h.lower().strip() for h in next(reader, [])}
        strong_signals = {"exercise name", "set order", "workout name", "reps"}
        if len(strong_signals & headers) >= 2 or "exercise name" in headers:
            return "strong"
        # Filename fallback
        name = filepath.stem.lower()
        if any(k in name for k in ["strong", "workout", "gym", "lift"]):
            return "strong"
    except Exception:
        pass
    return None


def main():
    initialize()
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)

    files = [f for f in sorted(IMPORTS_DIR.iterdir()) if not f.name.startswith(".")]
    if not files:
        print(f"No files found in {IMPORTS_DIR}")
        print("Drop your export files there and run this script again.")
        return

    print(f"Scanning {IMPORTS_DIR} ...\n")
    imported_any = False

    for f in files:
        print(f"  {f.name} ... ", end="", flush=True)
        suffix = f.suffix.lower()

        # --- MacroFactor Excel ---
        if suffix == ".xlsx":
            try:
                from src.ingestion.macrofactor import import_xlsx
                result = import_xlsx(f)
                sheets = ", ".join(result["sheets_imported"])
                print(
                    f"MacroFactor — {result['nutrition_rows']} nutrition rows, "
                    f"{result['weight_rows']} weight entries  "
                    f"[sheets: {sheets}]"
                )
                imported_any = True
            except Exception as e:
                print(f"ERROR: {e}")
            continue

        # --- Apple Health XML ---
        if suffix == ".xml":
            try:
                from src.ingestion.apple_health import import_xml
                result = import_xml(f)
                print(
                    f"Apple Health — {result['health_rows']} health days, "
                    f"{result['weight_rows']} weight entries"
                )
                imported_any = True
            except Exception as e:
                print(f"ERROR: {e}")
            continue

        # --- CSV files ---
        if suffix == ".csv":
            csv_type = sniff_csv_type(f)
            if csv_type == "strong":
                try:
                    from src.ingestion.strong import import_csv
                    result = import_csv(f)
                    print(f"Strong — {result['workouts']} workouts, {result['sets']} sets")
                    imported_any = True
                except Exception as e:
                    print(f"ERROR: {e}")
            else:
                print(
                    "skipped — unrecognised CSV. "
                    "Rename to include 'strong' if it's a gym export."
                )
            continue

        print("skipped (unrecognised file type)")

    if imported_any:
        print("\nImport complete. Your data is ready in Claude Desktop.")
    else:
        print("\nNo files were imported.")


if __name__ == "__main__":
    main()
    input("\nPress Enter to close...")
