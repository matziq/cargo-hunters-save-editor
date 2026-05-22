"""Refresh the Cargo Hunters editor item CSV from the latest local save.

The game does not store one simple, plaintext "all items" table in the Unity
Addressables bundles. The most reliable local source we have is the player's
latest ``offline.save`` because it contains current inventory items and synced
shop/pricelist commodity records.

This script merges that save data into ``all_items_detailed.csv``:

* preserves every existing detailed CSV row;
* adds built-in rows such as Cash/Lockpick when missing;
* discovers TemplateIds from inventory/equipment/shelter/mailbox/pricelists;
* uses shop ``DataId`` / prices where available to infer names and base prices;
* writes a refreshed CSV plus a small missing/new row report.

Typical use from this folder:

	python refresh_item_catalog_from_save.py
	python refresh_item_catalog_from_save.py --replace

For best results, open the latest game version first and visit/refresh the shops
so ``AccountPricelists`` in ``offline.save`` contains the newest commodities.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from save_io import BUILTIN_CATALOG_ROWS, DEFAULT_SAVE_PATH


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / "all_items_detailed.csv"
DEFAULT_OUT = SCRIPT_DIR / "all_items_detailed_refreshed.csv"
CSV_FIELDS = [
	"ItemName",
	"ItemID",
	"BasePrice",
	"SellPrice",
	"PriceCoefficient",
	"Weight",
	"Width",
	"Height",
	"InventorySize",
	"CategoryID",
	"SubcategoryID",
	"ShopName",
	"PriceSource",
]

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass
class Observation:
	template_id: str
	paths: set[str] = field(default_factory=set)
	names: set[str] = field(default_factory=set)
	prices: list[float] = field(default_factory=list)
	widths: list[int] = field(default_factory=list)
	heights: list[int] = field(default_factory=list)
	weights: list[float] = field(default_factory=list)
	categories: set[str] = field(default_factory=set)
	subcategories: set[str] = field(default_factory=set)
	shop_names: set[str] = field(default_factory=set)

	def add_item_dto(self, item: dict[str, Any], path: str) -> None:
		self.paths.add(path)
		additional = item.get("AdditionalData") or {}
		data = additional.get("_data") or {}
		for key in ("Name", "ItemName", "TemplateName", "DisplayName"):
			value = item.get(key) or data.get(key) or data.get(f"_{key}")
			if value:
				self.names.add(str(value))
		self._add_int(data, self.widths, "BaseComponent_width", "BaseComponent_w", "Width", "_width")
		self._add_int(data, self.heights, "BaseComponent_height", "BaseComponent_h", "Height", "_height")
		self._add_float(data, self.weights, "BaseComponent_weight", "Weight", "_weight")
		for key in ("CategoryID", "CategoryId", "ItemCategoryId", "_categoryId", "categoryId"):
			if data.get(key) not in (None, ""):
				self.categories.add(str(data[key]))
		for key in ("SubcategoryID", "SubcategoryId", "ItemSubcategoryId", "_subcategoryId", "subcategoryId"):
			if data.get(key) not in (None, ""):
				self.subcategories.add(str(data[key]))

	@staticmethod
	def _add_int(data: dict[str, Any], target: list[int], *keys: str) -> None:
		for key in keys:
			if key in data:
				try:
					value = int(data[key])
				except (TypeError, ValueError):
					continue
				if value > 0:
					target.append(value)

	@staticmethod
	def _add_float(data: dict[str, Any], target: list[float], *keys: str) -> None:
		for key in keys:
			if key in data:
				try:
					value = float(data[key])
				except (TypeError, ValueError):
					continue
				if value >= 0:
					target.append(value)


def _load_rows(csv_path: Path) -> list[dict[str, str]]:
	if not csv_path.exists():
		return []
	with csv_path.open("r", encoding="utf-8", newline="") as fh:
		return list(csv.DictReader(fh))


def _write_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
	csv_path.parent.mkdir(parents=True, exist_ok=True)
	with csv_path.open("w", encoding="utf-8", newline="") as fh:
		writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
		writer.writeheader()
		for row in rows:
			writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def _looks_like_guid(value: Any) -> bool:
	return isinstance(value, str) and GUID_RE.fullmatch(value) is not None


def _clean_data_id_name(data_id: str) -> str:
	# Examples: Day1Preset_SimpleItem_.45 ACP A-I_AccountLevel_1+
	value = data_id.strip()
	match = re.search(r"SimpleItem_(.+?)_AccountLevel", value)
	if match:
		value = match.group(1)
	value = re.sub(r"^Day\d+Preset_", "", value)
	value = re.sub(r"_AccountLevel.*$", "", value)
	value = value.replace("_", " ").strip()
	# Drop common shop tier suffixes from ammo names when possible.
	value = re.sub(r"\s+A-I$", "", value)
	if _looks_like_guid(value):
		return ""
	return value or data_id


def _cash_price(commodity: dict[str, Any]) -> float | None:
	for item in (commodity.get("Price") or {}).get("Items") or []:
		if item.get("ItemTemplateId") == "cb567810-cc82-424f-893f-299c704ffb12":
			try:
				return float(item.get("Count", 0))
			except (TypeError, ValueError):
				return None
	return None


def _observe_save(data: dict[str, Any]) -> dict[str, Observation]:
	observations: dict[str, Observation] = {}

	def obs(template_id: str) -> Observation:
		return observations.setdefault(template_id, Observation(template_id))

	def walk(value: Any, path: str = "") -> None:
		if isinstance(value, dict):
			template_id = value.get("TemplateId")
			if _looks_like_guid(template_id):
				obs(template_id).add_item_dto(value, path or "<root>")
			# Price rows use ItemTemplateId for both currency and barter items.
			item_template_id = value.get("ItemTemplateId")
			if _looks_like_guid(item_template_id):
				obs(item_template_id).paths.add(path or "<root>")
			for key, child in value.items():
				walk(child, f"{path}.{key}" if path else key)
		elif isinstance(value, list):
			for index, child in enumerate(value):
				walk(child, f"{path}[{index}]")

	walk(data)

	for pricelist_index, pricelist in enumerate(data.get("AccountPricelists") or []):
		for commodity_index, commodity in enumerate(pricelist.get("Commodities") or []):
			item = commodity.get("ItemDto") or {}
			template_id = item.get("TemplateId")
			if not _looks_like_guid(template_id):
				continue
			item_obs = obs(template_id)
			item_obs.add_item_dto(item, f"AccountPricelists[{pricelist_index}].Commodities[{commodity_index}].ItemDto")
			data_id = commodity.get("DataId")
			if data_id:
				name = _clean_data_id_name(str(data_id))
				if name:
					item_obs.names.add(name)
			price = _cash_price(commodity)
			if price is not None:
				item_obs.prices.append(price)
			item_obs.shop_names.add(f"Pricelist {pricelist_index}")

	return observations


def _first(values: list[Any] | set[Any], default: str = "") -> str:
	if isinstance(values, set):
		values = sorted(v for v in values if v not in (None, ""))
	if not values:
		return default
	return str(values[0])


def _best_price(prices: list[float]) -> str:
	if not prices:
		return "0"
	value = min(prices)
	return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _row_from_observation(observation: Observation) -> dict[str, str]:
	width = _first(observation.widths, "1")
	height = _first(observation.heights, "1")
	try:
		inventory_size = str(max(1, int(width)) * max(1, int(height)))
	except ValueError:
		inventory_size = "1"
	base_price = _best_price(observation.prices)
	try:
		sell_price = str(round(float(base_price) * 0.9, 2))
	except ValueError:
		sell_price = "0"
	name = _first(observation.names, f"Unknown {observation.template_id[:8]}")
	source = "Save/pricelist refresh" if observation.prices else "Observed in save"
	return {
		"ItemName": name,
		"ItemID": observation.template_id,
		"BasePrice": base_price,
		"SellPrice": sell_price,
		"PriceCoefficient": "0.90" if observation.prices else "",
		"Weight": _first(observation.weights, "0"),
		"Width": width,
		"Height": height,
		"InventorySize": inventory_size,
		"CategoryID": _first(observation.categories, "unknown"),
		"SubcategoryID": _first(observation.subcategories, "unknown"),
		"ShopName": _first(observation.shop_names, "Observed save"),
		"PriceSource": source,
	}


def refresh_catalog(save_path: Path, csv_path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
	data = json.loads(save_path.read_text(encoding="utf-8"))
	rows = _load_rows(csv_path)
	seen = {(row.get("ItemID") or "").strip() for row in rows}

	added: list[dict[str, str]] = []
	for row in BUILTIN_CATALOG_ROWS:
		if row["ItemID"] not in seen:
			rows.append(dict(row))
			added.append(dict(row))
			seen.add(row["ItemID"])

	observations = _observe_save(data)
	for template_id, observation in sorted(observations.items()):
		if template_id in seen:
			continue
		row = _row_from_observation(observation)
		rows.append(row)
		added.append(row)
		seen.add(template_id)

	rows.sort(key=lambda row: ((row.get("ItemName") or "").lower(), row.get("ItemID") or ""))
	return rows, added


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Merge latest offline.save item TemplateIds into all_items_detailed.csv.")
	parser.add_argument("--save", type=Path, default=DEFAULT_SAVE_PATH, help="Path to Cargo Hunters offline.save.")
	parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Existing item CSV to merge.")
	parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output refreshed CSV path.")
	parser.add_argument("--replace", action="store_true", help="Back up and replace --csv with the refreshed output.")
	return parser.parse_args()


def main() -> int:
	args = parse_args()
	if not args.save.exists():
		raise SystemExit(f"Save not found: {args.save}")
	rows, added = refresh_catalog(args.save, args.csv)
	out_path = args.csv if args.replace else args.out

	if args.replace and args.csv.exists():
		stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
		backup = args.csv.with_name(f"{args.csv.stem}.{stamp}.bak{args.csv.suffix}")
		shutil.copy2(args.csv, backup)
		print(f"Backed up existing CSV: {backup}")

	_write_rows(out_path, rows)
	report_path = out_path.with_name(f"{out_path.stem}_added_rows.csv")
	_write_rows(report_path, added)

	print(f"Read save: {args.save}")
	print(f"Read CSV:  {args.csv if args.csv.exists() else '<new>'}")
	print(f"Wrote:     {out_path}")
	print(f"Rows:      {len(rows)} total, {len(added)} added")
	print(f"Added row report: {report_path}")
	if added:
		print("\nAdded items:")
		for row in added[:50]:
			print(f"  {row['ItemName']:<32} {row['ItemID']}  source={row['PriceSource']}")
		if len(added) > 50:
			print(f"  ... and {len(added) - 50} more")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
