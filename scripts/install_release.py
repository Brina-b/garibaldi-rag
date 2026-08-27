from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

MANIFEST_NAMES = {"manifest.json", "release_manifest.json"}


def merge_manifest(base_manifest: dict, patch: dict) -> dict:
    by_id = {row["document_id"]: row for row in base_manifest.get("documents", [])}
    for row in patch.get("documents", []):
        by_id[row["document_id"]] = row
    base_manifest["documents"] = sorted(by_id.values(), key=lambda row: (row.get("act", 0), row["document_id"]))
    base_manifest["available_acts"] = sorted(
        {int(x) for x in base_manifest.get("available_acts", []) + patch.get("available_acts", [])}
    )
    base_manifest["last_release_id"] = patch.get("release_id", base_manifest.get("last_release_id"))
    return base_manifest


def install(archive: Path, data_dir: Path) -> tuple[int, list[int] | None]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp_path)

        extracted_data = tmp_path / "data"
        if not extracted_data.is_dir():
            raise SystemExit(
                "Nello zip non c'è una cartella data/ alla radice: non sembra un release pack valido per questa starter."
            )

        copied = 0
        for source in extracted_data.rglob("*"):
            if not source.is_file() or source.name in MANIFEST_NAMES:
                continue
            target = data_dir / source.relative_to(extracted_data)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

        release_patch = extracted_data / "release_manifest.json"
        full_manifest = extracted_data / "manifest.json"
        base_manifest_path = data_dir / "manifest.json"

        if release_patch.exists():
            base = (
                json.loads(base_manifest_path.read_text(encoding="utf-8"))
                if base_manifest_path.exists()
                else {"schema_version": "1.0", "track": "standard", "available_acts": [], "documents": []}
            )
            patch = json.loads(release_patch.read_text(encoding="utf-8"))
            merged = merge_manifest(base, patch)
            base_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            base_manifest_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            acts = merged["available_acts"]
        elif full_manifest.exists():
            base_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full_manifest, base_manifest_path)
            acts = json.loads(full_manifest.read_text(encoding="utf-8")).get("available_acts")
        else:
            acts = None

    return copied, acts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Installa un release pack (kickoff completo o incrementale) nella starter repo."
    )
    parser.add_argument("archive", type=Path, help="Zip scaricato dalla piattaforma, es. act_4.zip")
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()

    copied, acts = install(args.archive, args.data)

    print(f"Copiati {copied} file in {args.data}/.")
    if acts is not None:
        print(f"Manifest aggiornato: Act disponibili {acts}.")
    else:
        print("Nello zip non c'era nessun manifest.json: aggiornalo a mano se serve.")
    print("Ricostruisci l'indice e la submission con:")
    print("  python baseline_naive_rag.py --questions <domande.json> --round-id <id> --output outputs/submission.json --rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
