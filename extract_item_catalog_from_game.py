"""Extract Cargo Hunters item templates and shop inventories from game files.

The authoritative local item data is stored as JSON TextAssets inside the
``repositoriesgroup_assets_all_*.bundle`` Addressables bundle. This script reads
that bundle with UnityPy and writes:

* ``all_items_detailed_from_game.csv`` — full item template catalog
* ``shop_inventories_from_game.csv`` — shop/offline pricelist commodities

Typical use from this folder:

	python extract_item_catalog_from_game.py --install-deps
	python extract_item_catalog_from_game.py
	python extract_item_catalog_from_game.py --replace

``--replace`` backs up and replaces ``all_items_detailed.csv`` for the editor.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GAME_DIR = Path(r"D:\Games\Cargo.Hunters.v0.26.26.43")
DEFAULT_CSV = SCRIPT_DIR / "all_items_detailed.csv"
DEFAULT_ITEMS_OUT = SCRIPT_DIR / "all_items_detailed_from_game.csv"
DEFAULT_SHOPS_OUT = SCRIPT_DIR / "shop_inventories_from_game.csv"
CASH_TEMPLATE_ID = "cb567810-cc82-424f-893f-299c704ffb12"

ITEM_FIELDS = [
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
	"StackCapacity",
	"VisualName",
	"CatalogVisualPath",
	"IconVisualName",
	"CatalogIconPath",
	"DroppedVisualName",
	"CatalogDroppedPath",
]

SHOP_FIELDS = [
	"SourceAsset",
	"ShopId",
	"ShopAlias",
	"CommodityId",
	"ItemTemplateId",
	"ItemName",
	"Count",
	"BasePrice",
	"BuyFromShopPriceCoefficient",
	"BuyPrice",
	"Chance",
	"PositionViewPriority",
	"ContainerContent",
]


@dataclass
class ItemTemplate:
	template_id: str
	name: str = ""
	weight: str = "0"
	width: str = "1"
	height: str = "1"
	category_id: str = ""
	subcategory_id: str = ""
	base_price: str = "0"
	stack_capacity: str = ""
	visual_name: str = ""
	catalog_visual_path: str = ""
	icon_visual_name: str = ""
	catalog_icon_path: str = ""
	dropped_visual_name: str = ""
	catalog_dropped_path: str = ""
	shop_aliases: list[str] = field(default_factory=list)
	price_coefficients: list[float] = field(default_factory=list)

	@property
	def inventory_size(self) -> str:
		try:
			return str(max(1, int(self.width)) * max(1, int(self.height)))
		except ValueError:
			return "1"

	def to_csv_row(self) -> dict[str, str]:
		coefficient = self.price_coefficients[0] if self.price_coefficients else 1.0
		try:
			sell_price = float(self.base_price) * coefficient
			sell_price_text = _format_number(sell_price)
		except ValueError:
			sell_price_text = "0"
		return {
			"ItemName": self.name or f"Unknown {self.template_id[:8]}",
			"ItemID": self.template_id,
			"BasePrice": self.base_price,
			"SellPrice": sell_price_text,
			"PriceCoefficient": f"{coefficient:.2f}",
			"Weight": self.weight,
			"Width": self.width,
			"Height": self.height,
			"InventorySize": self.inventory_size,
			"CategoryID": self.category_id,
			"SubcategoryID": self.subcategory_id,
			"ShopName": self.shop_aliases[0] if self.shop_aliases else "None (default)",
			"PriceSource": "Repository TextAsset",
			"StackCapacity": self.stack_capacity,
			"VisualName": self.visual_name,
			"CatalogVisualPath": self.catalog_visual_path,
			"IconVisualName": self.icon_visual_name,
			"CatalogIconPath": self.catalog_icon_path,
			"DroppedVisualName": self.dropped_visual_name,
			"CatalogDroppedPath": self.catalog_dropped_path,
		}


def _format_number(value: float) -> str:
	if value.is_integer():
		return str(int(value))
	return f"{value:.4f}".rstrip("0").rstrip(".")


def _ensure_unitypy(install_deps: bool) -> None:
	if importlib.util.find_spec("UnityPy") is not None:
		return
	if not install_deps:
		raise SystemExit(
			"UnityPy is not installed. Re-run with --install-deps, or install it manually with:\n"
			f"  {sys.executable} -m pip install UnityPy"
		)
	subprocess.check_call([sys.executable, "-m", "pip", "install", "UnityPy"])


def _bundles_dir_from_game_dir(game_dir: Path) -> Path:
	return game_dir / "CargoHunters_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64"


def _catalog_json_from_game_dir(game_dir: Path) -> Path:
	return game_dir / "CargoHunters_Data" / "StreamingAssets" / "aa" / "catalog.json"


def _find_repository_bundle(bundles_dir: Path) -> Path:
	matches = sorted(bundles_dir.glob("repositoriesgroup_assets_all_*.bundle"))
	if not matches:
		raise SystemExit(f"Could not find repositoriesgroup_assets_all_*.bundle under {bundles_dir}")
	return matches[0]


def _load_repository_assets(bundle_path: Path) -> dict[str, Any]:
	import UnityPy  # type: ignore[import-not-found]

	env = UnityPy.load(str(bundle_path))
	assets: dict[str, Any] = {}
	for obj in env.objects:
		type_name = getattr(getattr(obj, "type", None), "name", "")
		if type_name != "TextAsset":
			continue
		data = obj.read()
		name = getattr(data, "name", None) or getattr(data, "m_Name", None) or ""
		raw = getattr(data, "script", None) or getattr(data, "m_Script", None) or b""
		text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
		if name in {"item_templates", "shop_templates", "offline_pricelist"}:
			assets[name] = json.loads(text)
	missing = {"item_templates", "shop_templates", "offline_pricelist"} - set(assets)
	if missing:
		raise SystemExit(f"Repository bundle is missing expected TextAssets: {', '.join(sorted(missing))}")
	return assets


def _normalize_addressable_path(value: object) -> str:
	return re.sub(r"[\\/]+", "/", str(value or "").strip()).lower()


def _load_catalog_paths(catalog_json: Path | None) -> list[str]:
	if catalog_json is None or not catalog_json.exists():
		return []
	try:
		data = json.loads(catalog_json.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return []
	paths = [str(value) for value in data.get("m_InternalIds") or []]
	return sorted(path for path in paths if path.startswith("Assets/"))


def _catalog_path_for(relative_path: str, catalog_paths: list[str]) -> str:
	if not relative_path or not catalog_paths:
		return ""
	rel = _normalize_addressable_path(relative_path)
	candidates = [rel]
	if not rel.startswith("assets/"):
		candidates.append(f"assets/prefabs/items/{rel}")
	if rel.startswith("items/"):
		candidates.append(f"assets/prefabs/{rel}")
	for candidate in candidates:
		for path in catalog_paths:
			if _normalize_addressable_path(path) == candidate:
				return path
	for candidate in candidates:
		for path in catalog_paths:
			if _normalize_addressable_path(path).endswith(candidate):
				return path
	return ""


def _component_data(template: dict[str, Any], type_id: int) -> list[dict[str, Any]]:
	out: list[dict[str, Any]] = []
	for component in template.get("_components") or []:
		if component.get("$t") == type_id:
			out.append(component.get("_data") or component)
	return out


def _cash_price_from_price_data(price_data: dict[str, Any]) -> float | None:
	price = price_data.get("Price") if isinstance(price_data, dict) else None
	for item in (price or {}).get("Items") or []:
		if item.get("ItemTemplateId") == CASH_TEMPLATE_ID:
			try:
				return float(item.get("Count", 0))
			except (TypeError, ValueError):
				return None
	return None


def _extract_templates(item_templates: list[dict[str, Any]]) -> dict[str, ItemTemplate]:
	items: dict[str, ItemTemplate] = {}
	for raw in item_templates:
		template_id = raw.get("_id")
		if not template_id:
			continue
		item = ItemTemplate(template_id=template_id)
		for component in raw.get("_components") or []:
			component_type = component.get("$t")
			data = component.get("_data") or {}
			if component_type == 26276:
				item.name = str(component.get("Name") or item.name)
			elif component_type == 4373:
				size = data.get("Size") or {}
				item.weight = _format_number(float(data.get("Mass", 0))) if data.get("Mass") is not None else "0"
				item.width = str(size.get("Width") or 1)
				item.height = str(size.get("Height") or 1)
				item.visual_name = str(data.get("VisualName") or "")
				item.icon_visual_name = str(data.get("IconVisualName") or "")
				item.dropped_visual_name = str(data.get("DroppedVisualName") or "")
			elif component_type == 1204:
				item.category_id = str(data.get("CategoryId") or "")
				item.subcategory_id = str(data.get("SubCategoryId") or "")
			elif component_type == 24348:
				if data.get("StackCapacity") not in (None, ""):
					item.stack_capacity = str(data.get("StackCapacity"))
		prices: list[float] = []
		for price_component_type in (18789, 51833):
			for price_data in _component_data(raw, price_component_type):
				value = _cash_price_from_price_data(price_data)
				if value is not None:
					prices.append(value)
		if template_id == CASH_TEMPLATE_ID:
			item.name = "Cash"
			item.base_price = "1"
		elif prices:
			item.base_price = _format_number(max(prices))
		items[template_id] = item
	return items


def _apply_catalog_paths(items: dict[str, ItemTemplate], catalog_paths: list[str]) -> None:
	if not catalog_paths:
		return
	for item in items.values():
		item.catalog_visual_path = _catalog_path_for(item.visual_name, catalog_paths)
		item.catalog_icon_path = _catalog_path_for(item.icon_visual_name, catalog_paths)
		item.catalog_dropped_path = _catalog_path_for(item.dropped_visual_name, catalog_paths)


def _item_template_id(item_ref: dict[str, Any]) -> str:
	return str(item_ref.get("ItemTemplateId") or item_ref.get("ItemItemplateId") or "")


def _container_content(item_ref: dict[str, Any]) -> str:
	ids = [_item_template_id(child) for child in item_ref.get("ContainerContent") or []]
	return ";".join(item_id for item_id in ids if item_id)


def _iter_shop_commodities(assets: dict[str, Any]) -> Iterable[tuple[str, str, str, dict[str, Any]]]:
	for shop in assets.get("shop_templates") or []:
		shop_id = str(shop.get("Id") or "")
		alias = str(shop.get("Alias") or shop_id)
		for commodity in ((shop.get("PricelistTemplate") or {}).get("Commodities") or []):
			yield "shop_templates", shop_id, alias, commodity
	for index, pricelist in enumerate(assets.get("offline_pricelist") or []):
		shop_id = str(pricelist.get("Id") or f"offline_{index}")
		alias = f"offline_{shop_id[:8]}"
		for commodity in ((pricelist.get("PricelistTemplate") or {}).get("Commodities") or []):
			yield "offline_pricelist", shop_id, alias, commodity


def _extract_shop_rows(assets: dict[str, Any], items: dict[str, ItemTemplate]) -> list[dict[str, str]]:
	rows: list[dict[str, str]] = []
	for source, shop_id, alias, commodity in _iter_shop_commodities(assets):
		item_ref = commodity.get("Item") or {}
		template_id = _item_template_id(item_ref)
		if not template_id:
			continue
		item = items.get(template_id)
		coefficient = float(commodity.get("BuyFromShopPriceCoefficient") or 1.0)
		if item is not None:
			if alias not in item.shop_aliases:
				item.shop_aliases.append(alias)
			item.price_coefficients.append(coefficient)
			item_name = item.name or f"Unknown {template_id[:8]}"
			base_price = item.base_price
		else:
			item_name = f"Unknown {template_id[:8]}"
			base_price = "0"
		try:
			buy_price = _format_number(float(base_price) * coefficient)
		except ValueError:
			buy_price = "0"
		rows.append({
			"SourceAsset": source,
			"ShopId": shop_id,
			"ShopAlias": alias,
			"CommodityId": str(commodity.get("Id") or ""),
			"ItemTemplateId": template_id,
			"ItemName": item_name,
			"Count": str(commodity.get("Count") if commodity.get("Count") is not None else ""),
			"BasePrice": base_price,
			"BuyFromShopPriceCoefficient": f"{coefficient:.2f}",
			"BuyPrice": buy_price,
			"Chance": str(commodity.get("Chance") if commodity.get("Chance") is not None else ""),
			"PositionViewPriority": str(commodity.get("PositionViewPriority") if commodity.get("PositionViewPriority") is not None else ""),
			"ContainerContent": _container_content(item_ref),
		})
	rows.sort(key=lambda row: (row["ShopAlias"], row["PositionViewPriority"].zfill(6), row["ItemName"]))
	return rows


def _write_csv(path: Path, rows: Iterable[dict[str, str]], fields: list[str]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8", newline="") as fh:
		writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
		writer.writeheader()
		for row in rows:
			writer.writerow({field: row.get(field, "") for field in fields})


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Extract Cargo Hunters item catalog/shop inventories from game repository assets.")
	parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR, help="Cargo Hunters install folder.")
	parser.add_argument("--bundles-dir", type=Path, default=None, help="Override Addressables StandaloneWindows64 folder.")
	parser.add_argument("--items-out", type=Path, default=DEFAULT_ITEMS_OUT, help="Output item catalog CSV.")
	parser.add_argument("--shops-out", type=Path, default=DEFAULT_SHOPS_OUT, help="Output shop inventory CSV.")
	parser.add_argument("--catalog-json", type=Path, default=None, help="Optional Addressables catalog.json used to add full prefab paths.")
	parser.add_argument("--replace", action="store_true", help="Back up and replace all_items_detailed.csv with extracted item catalog.")
	parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="CSV path to replace when --replace is used.")
	parser.add_argument("--install-deps", action="store_true", help="Install UnityPy if missing.")
	return parser.parse_args()


def main() -> int:
	args = parse_args()
	_ensure_unitypy(args.install_deps)
	bundles_dir = args.bundles_dir or _bundles_dir_from_game_dir(args.game_dir)
	catalog_json = args.catalog_json or _catalog_json_from_game_dir(args.game_dir)
	bundle = _find_repository_bundle(bundles_dir)
	assets = _load_repository_assets(bundle)
	items = _extract_templates(assets["item_templates"])
	catalog_paths = _load_catalog_paths(catalog_json)
	_apply_catalog_paths(items, catalog_paths)
	shop_rows = _extract_shop_rows(assets, items)
	item_rows = [item.to_csv_row() for item in items.values()]
	item_rows.sort(key=lambda row: ((row.get("ItemName") or "").lower(), row.get("ItemID") or ""))

	items_out = args.csv if args.replace else args.items_out
	if args.replace and args.csv.exists():
		stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
		backup = args.csv.with_name(f"{args.csv.stem}.{stamp}.bak{args.csv.suffix}")
		shutil.copy2(args.csv, backup)
		print(f"Backed up existing CSV: {backup}")

	_write_csv(items_out, item_rows, ITEM_FIELDS)
	_write_csv(args.shops_out, shop_rows, SHOP_FIELDS)
	print(f"Repository bundle: {bundle}")
	print(f"Addressables catalog: {catalog_json if catalog_paths else 'not used/found'}")
	print(f"Catalog paths:     {len(catalog_paths)}")
	print(f"Item templates:    {len(item_rows)} -> {items_out}")
	print(f"Shop commodities:  {len(shop_rows)} -> {args.shops_out}")
	print(f"Shop aliases:      {', '.join(sorted({row['ShopAlias'] for row in shop_rows}))}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
