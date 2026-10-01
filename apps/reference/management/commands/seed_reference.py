import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.reference import services

SEED_DIR = Path(__file__).resolve().parents[2] / "seed_data"


class Command(BaseCommand):
    help = "Load/refresh crafts and locations from JSON. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--dir", type=Path, default=SEED_DIR)

    def handle(self, *args, dir: Path, **options):
        crafts = json.loads((dir / "crafts.json").read_text())
        locations = json.loads((dir / "locations.json").read_text())
        for label, result in (
            ("crafts", services.sync_crafts(crafts)),
            ("locations", services.sync_locations(locations)),
        ):
            self.stdout.write(f"{label}: {result.created} created, {result.updated} updated")
