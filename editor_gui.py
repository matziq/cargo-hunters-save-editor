"""Tkinter GUI for the Cargo Hunters offline.save editor.

Wraps add_item.add_items() with a CSV-driven item picker.

Features
--------
* Load items from all_items_detailed.csv (search by name / id / category).
* Pick a save file (defaults to offline.save next to this script).
* Enter qty / count / condition / durability.
* Add button -> calls add_item.add_items() (which writes a timestamped backup).
* Output pane mirrors the prints from add_item.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import re
import subprocess
import sys
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, Optional

from add_item import add_items
from save_io import (
    Container,
    discover_containers,
    get_experience,
    get_items_list,
    get_skills,
    list_save_backups,
    load_save,
    load_template_dims,
    load_template_names,
    move_items_to_container,
    prune_old_backups,
    remove_items_by_ids,
    set_experience,
    set_items_condition_durability_full,
    set_nickname,
    set_skill_levels,
    set_skill_points,
    split_stack_item,
    DEFAULT_SAVE_DIR,
    DEFAULT_SAVE_PATH,
    BUILTIN_CATALOG_ROWS,
    CONDITION_FULL_VALUE,
    get_stack_count_max,
    get_stack_count_max_for_template,
    get_use_count_max,
    write_save,
)

IS_FROZEN = getattr(sys, "frozen", False)
SCRIPT_DIR = Path(__file__).resolve().parent
# When frozen by PyInstaller, _MEIPASS points at the temp extraction dir that
# holds bundled data (CSV, icons). The user-facing directory next to the .exe
# is used for editable files (settings, the active CSV the user might replace).
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR))
USER_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else SCRIPT_DIR


def _resource_path(rel: str) -> Path:
    """Resolve a read-only data file: prefer USER_DIR override, then bundle."""
    user_copy = USER_DIR / rel
    if user_copy.exists():
        return user_copy
    return BUNDLE_DIR / rel


DEFAULT_SAVE = DEFAULT_SAVE_PATH
DEFAULT_CSV = _resource_path("all_items_detailed.csv")
SETTINGS_PATH = USER_DIR / "editor_settings.json"
SPRITE_ICON_DIR = _resource_path("exported_icons/Sprite")
DEFAULT_ICON_DIR = SPRITE_ICON_DIR
FALLBACK_ICON_DIRS: tuple[Path, ...] = ()
DEFAULT_GAME_DIR = Path(r"D:\Games\Cargo.Hunters.v0.26.26.43")
CURRENT_INVENTORY_SOURCES = ("inventory", "equipment")
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
GUID_SEARCH_FIELD_NAMES = {"id", "itemid", "item id", "templateid", "template id", "template", "instanceid", "instance id", "instance"}
DATAMETER_SEARCH_ALIASES = ("C-METER", "CO-METER", "C Meter", "CO Meter", "Datameter", "Data meter")

VIEW_THEME = {
    "add": {
        "bg": "#eff6ff",
        "panel": "#dbeafe",
        "fg": "#1e3a8a",
        "button": "#2563eb",
        "button_active": "#1d4ed8",
        "name": "Add Items",
    },
    "inventory": {
        "bg": "#ecfdf5",
        "panel": "#d1fae5",
        "fg": "#065f46",
        "button": "#059669",
        "button_active": "#047857",
        "name": "Current Inventory",
    },
    "character": {
        "bg": "#faf5ff",
        "panel": "#ede9fe",
        "fg": "#5b21b6",
        "button": "#7c3aed",
        "button_active": "#6d28d9",
        "name": "Character",
    },
}

CATEGORY_ICON_BY_ID = {
    "1": "Icon_Weapons",
    "2": "Icon_Pistol",
    "3": "Icon_Ammo",
    "5": "Icon_Backpacks",
    "6": "Icon_ChestRigs",
    "7": "Icon_Headgear",
    "8": "Icon_Equipment",
    "9": "Icon_PlateCarriers",
    "10": "Icon_Grenades",
    "11": "Icon_Melee",
    "13": "Icon_Bodyparts",
    "14": "Icon_Aid_Kits",
    "17": "Icon_Tools",
    "18": "Category_Utility_Secondary",
    "20": "Icon_WeaponParts",
    "21": "Icon_Valuables",
    "22": "Icon_Cases",
    "23": "Icon_Keys",
    "24": "Icon_Tools",
    "30": "Icon_Ammo",
    "31": "Icon_Devices",
    "33": "Icon_Surplus",
    "36": "Icon_Resources",
    "37": "Icon_Devices",
}

# Category IDs whose items act as containers (hold other items in a grid).
# Used to classify the inventory Source column ("Equipment" vs "Items") and
# to surface empty containers (e.g. a vest with nothing in it) in the view.
#   5  = Backpacks
#   6  = ChestRigs
#   8  = Equipment (holsters, pouches, slings)
#   9  = PlateCarriers
#   12 = Internal grids (Inventory_N, Storage_N, Safestash_page_N, Shelter
#        storage modules) — these are sub-pages of larger containers that
#        physically hold the items in the save's flat list.
#   22 = Cases
CONTAINER_CATEGORY_IDS = {"5", "6", "8", "9", "12", "22"}

# Category IDs for weapon roots (whole guns) and their attachments/parts.
# Weapons are physically built from multiple linked items (receiver, barrel,
# mag, sight, suppressor, butt, etc.); the user perceives them as one item.
# Equipment view hides weapon parts and shows the gun as a single flat row.
# Items view keeps the gun expandable with its part tree nested underneath.
WEAPON_CATEGORY_ID = "1"
WEAPON_PART_CATEGORY_ID = "20"

# Short, readable names for CategoryID values used by Cargo Hunters. Used by
# the "Cat" column in the Current Inventory view (and the hover tooltip) so
# the user sees "Ammo" instead of "3" or "Grenade" instead of "10". Unknown
# ids fall back to the bare numeric id via `_category_name`.
CATEGORY_NAME_BY_ID = {
    "1": "Weapon",
    "3": "Ammo",
    "5": "Backpack",
    "6": "ChestRig",
    "7": "Clothing",
    "8": "Equipment Case",
    "9": "Armor",
    "10": "Grenade",
    "11": "Melee",
    "12": "Inv Grid",
    "13": "Body Model",
    "14": "Repair Kit",
    "17": "Material",
    "18": "Quest Item",
    "20": "Weapon Part",
    "21": "Currency",
    "22": "Storage",
    "23": "Key",
    "24": "Tool",
    "26": "Droid",
    "27": "World Object",
    "28": "Marker",
    "29": "Auth Chip",
    "30": "Mod Tier",
    "31": "Skill",
    "33": "Hazard",
    "34": "Body Mod Cat",
    "35": "Camo",
    "36": "Skill Slot",
    "37": "Blueprint",
}

INVENTORY_VIEW_MODES = ("Equipment", "Items")
INVENTORY_SOURCE_EQUIPMENT_LABEL = "Equipment"
INVENTORY_SOURCE_ITEMS_LABEL = "Items"

# Category ID → treeview tag name for row colour coding.
# Blue   = weapons, ammo, combat
# Green  = body parts
# Red    = medical / repair kits
# Orange = materials, chemicals, crafting parts
# Teal   = containers (backpacks, rigs, cases, storage, internal grids)
# Gold   = currency / cash
# Purple = wearables (clothing, armor, body mod categories, camo)
# Pink   = special items (quest, keys, markers, auth chips)
# Gray   = misc world / metadata (droids, world objects, skills)
CATEGORY_COLOR_TAG: dict[str, str] = {
    "1": "cat_blue",   # Weapon
    "3": "cat_blue",   # Ammo
    "10": "cat_blue",  # Grenade
    "11": "cat_blue",  # Melee
    "20": "cat_blue",  # Weapon Part
    "30": "cat_blue",  # Mod Tier / ammo variant
    "13": "cat_green", # Body Parts
    "14": "cat_red",   # Repair Kit / Aid Kit
    "17": "cat_orange", # Material
    "24": "cat_orange", # Tool
    "33": "cat_orange", # Hazard / Chemical
    "36": "cat_orange", # Resources / Skill Slot
    "37": "cat_orange", # Blueprint
    "5": "cat_teal",   # Backpack
    "6": "cat_teal",   # ChestRig
    "8": "cat_teal",   # Equipment Case
    "12": "cat_teal",  # Inv Grid (internal storage page)
    "22": "cat_teal",  # Storage
    "21": "cat_gold",  # Currency
    "7": "cat_purple", # Clothing
    "9": "cat_purple", # Armor
    "34": "cat_purple",# Body Mod Cat
    "35": "cat_purple",# Camo
    "18": "cat_pink",  # Quest Item
    "23": "cat_pink",  # Key
    "28": "cat_pink",  # Marker
    "29": "cat_pink",  # Auth Chip
    "26": "cat_gray",  # Droid
    "27": "cat_gray",  # World Object
    "31": "cat_gray",  # Skill
}

SPECIAL_ICON_ALIASES = {
    "12x70": ("Ammo_12_Piercing",),
    "12x70 A-II": ("Ammo_12_Piercing", "BoxAmmo_12Reg"),
    "12x70 A-III": ("Ammo_12_Piercing", "BoxAmmo_12Reg"),
    "12x70 AP": ("Ammo_12_PiercingAP", "Ammo_12_Piercing", "BoxAmmo_12AP"),
    "12x70 B-II": ("Ammo_12_PiercingAP", "BoxAmmo_12AP", "Ammo_12_Piercing"),
    "12x70 B-III": ("Ammo_12_PiercingAP", "BoxAmmo_12AP", "Ammo_12_Piercing"),
    "12x70 C-II": ("Ammo_12_PiercingEXP", "BoxAmmo_12HP", "Ammo_12_Piercing"),
    "12x70 C-III": ("Ammo_12_PiercingEXP", "BoxAmmo_12HP", "Ammo_12_Piercing"),
    "12x70 E": ("Ammo_12_PiercingEXP", "BoxAmmo_12HP", "Ammo_12_Piercing"),
    "12x70NL": ("Ammo_12NL",),
    "Body Mods/Arms": ("Icon_Arms", "Armor_Arms", "Customization_Rig_Arm_Right", "Customization_Rig_Arm_Left"),
    "Body Mods/Heads": ("Icon_Head", "Armor_Head", "Icon_Headgear"),
    "Body Mods/Legs": ("Icon_Legs", "Armor_Legs", "Customization_Rig_Leg_Right", "Customization_Rig_Leg_Left"),
    "Body Mods/Torsos": ("Armor_Torso", "Customization_Rig_Torso", "Icon_BodyMods"),
    "Body Mods": ("Icon_BodyMods", "Icon_Bodyparts"),
}
MARKET_CATEGORY_ICON_ALIASES = {
    "Ammo": ("Icon_Ammo",),
    "Body Parts/Arms": ("Icon_Arms", "Icon_Bodyparts"),
    "Body Parts/Heads": ("Icon_Head", "Icon_Bodyparts"),
    "Body Parts/Legs": ("Icon_Legs", "Icon_Bodyparts"),
    "Body Parts/Torsos": ("Icon_Torso", "Icon_Bodyparts"),
    "Body Parts": ("Icon_Bodyparts",),
    "Consumables/Aids": ("Icon_Aid_Kits", "Icon_Consumables"),
    "Consumables/Generator Fuel": ("Icon_Fuel", "Icon_Consumables"),
    "Consumables/Grinder Disks": ("Icon_GrinderDiscs", "Icon_Consumables"),
    "Consumables/Keys": ("Icon_Keys", "Icon_Consumables"),
    "Consumables": ("Icon_Consumables",),
    "Equipment/Backpacks": ("Icon_Backpacks", "Icon_Equipment"),
    "Equipment/Chest Rigs": ("Icon_ChestRigs", "Icon_Equipment"),
    "Equipment/Headgear": ("Icon_Headgear", "Icon_Equipment"),
    "Equipment/Plate Carriers": ("Icon_PlateCarriers", "Icon_Equipment"),
    "Equipment": ("Icon_Equipment",),
    "Explosives/Grenades": ("Icon_Grenades",),
    "Explosives/Parts": ("Icon_ExplosivesParts",),
    "Explosives": ("Icon_Grenades", "Icon_Throwables"),
    "Instruments/Melee": ("Icon_Melee", "Icon_Instruments"),
    "Instruments": ("Icon_Instruments",),
    "Resources/A&D": ("Icon_AnDComponents", "Icon_Resources"),
    "Resources/Chemicals": ("Icon_Chemicals", "Icon_Resources"),
    "Resources/Devices": ("Icon_Devices", "Icon_Resources"),
    "Resources/Fabrics": ("Icon_Fabrics", "Icon_Resources"),
    "Resources/Materials": ("Icon_Materials", "Icon_Resources"),
    "Resources/Organics": ("Icon_Organics", "Icon_Resources"),
    "Resources/Papers": ("Icon_Papers", "Icon_Resources"),
    "Resources/Reagents": ("Icon_Reagents", "Icon_Resources"),
    "Resources/Surplus": ("Icon_Surplus", "Icon_Resources"),
    "Resources/Textile": ("Icon_Textile", "Icon_Resources"),
    "Resources/Tools": ("Icon_Tools", "Icon_Resources"),
    "Resources/Valuables": ("Icon_Valuables", "Icon_Resources"),
    "Resources/Ware": ("Icon_Ware", "Icon_Resources"),
    "Resources/Waste": ("Icon_Waste", "Icon_Resources"),
    "Resources": ("Icon_Resources",),
    "Root Market Category": ("Icon_All",),
    "Weapon Parts/Barrels": ("Icon_WeaponParts",),
    "Weapon Parts/Butts": ("Icon_WeaponParts",),
    "Weapon Parts/Grips": ("Icon_WeaponParts",),
    "Weapon Parts/Magazines": ("Icon_WeaponParts",),
    "Weapon Parts/Muzzle": ("Icon_WeaponParts",),
    "Weapon Parts/Sights": ("Icon_WeaponParts",),
    "Weapon Parts/Slides": ("Icon_WeaponParts",),
    "Weapon Parts/Tactical Handles": ("Icon_WeaponParts",),
    "Weapon Parts": ("Icon_WeaponParts",),
    "Weapons/Assault Riffles": ("Icon_AR", "Icon_Weapons"),
    "Weapons/Heavy Machine Guns": ("Icon_HMG", "Icon_Weapons"),
    "Weapons/Heavy Sniper Rifles": ("Icon_HSR", "Icon_Weapons"),
    "Weapons/Machine Guns": ("Icon_LMG", "Icon_Weapons"),
    "Weapons/Pistols": ("Icon_Pistol", "Icon_Weapons"),
    "Weapons/Shotguns": ("Icon_Shotgun", "Icon_Weapons"),
    "Weapons/Sniper Rifles": ("Icon_SR", "Icon_Weapons"),
    "Weapons/Submachine Guns": ("Icon_SMG", "Icon_Weapons"),
    "Weapons": ("Icon_Weapons",),
}
VISUAL_CATEGORY_ICON_ALIASES = {
    "Ammo": ("Icon_Ammo",),
    "ANFO": ("Icon_ExplosivesParts", "Icon_Resources"),
    "Anticor": ("Icon_Chemicals", "Icon_Resources"),
    "Armor": ("Category_Armor", "Icon_PlateCarriers"),
    "ArmorPlate": ("Icon_PlateCarriers", "Category_Armor"),
    "ArmorVests": ("Icon_PlateCarriers", "Category_Armor"),
    "AssaultRifles": ("Icon_AR", "Icon_Weapons"),
    "AssaultVests": ("Icon_ChestRigs", "Icon_Equipment"),
    "Backpacks": ("Icon_Backpacks", "Category_Backpack"),
    "BodyParts": ("Icon_Bodyparts",),
    "BottleGlass_05L": ("Icon_Ware", "Icon_Resources"),
    "Can": ("Icon_Ware", "Icon_Resources"),
    "Can_S": ("Icon_Ware", "Icon_Resources"),
    "Cases": ("Icon_Cases", "Category_Safe"),
    "CoilPlastic": ("Icon_Materials", "Icon_Resources"),
    "CupPlastic": ("Icon_Ware", "Icon_Resources"),
    "Customization": ("Icon_BodyMods",),
    "Droid": ("Icon_Devices", "Icon_AnDComponents"),
    "DuctTape": ("Icon_Tools", "Icon_Resources"),
    "Fibers": ("Icon_Fabrics", "Icon_Resources"),
    "FloppyDisk_01_S": ("Icon_Devices", "Icon_Papers"),
    "GoldBullion": ("Icon_Valuables",),
    "Granulate": ("Icon_Materials", "Icon_Resources"),
    "Grenade": ("Icon_Grenades",),
    "GunParts": ("Icon_WeaponParts",),
    "Gunpowder": ("Icon_ExplosivesParts", "Icon_Resources"),
    "Hats": ("Icon_Headgear", "Category_Headgear"),
    "HeavyGuns": ("Icon_HMG", "Icon_Weapons"),
    "Helmets": ("Icon_Headgear", "Category_Headgear"),
    "IndustrialSolventBottle": ("Icon_Chemicals", "Icon_Resources"),
    "Injector": ("Icon_Aid_Kits", "Icon_Consumables"),
    "Loot": ("Icon_Resources", "Icon_Valuables"),
    "Machineguns": ("Icon_LMG", "Icon_Weapons"),
    "Manual": ("Icon_Papers",),
    "MarksmanRifles": ("Icon_HSR", "Icon_SR", "Icon_Weapons"),
    "MetalParts": ("Icon_Materials", "Icon_Resources"),
    "MetalPowder_3DPrinter": ("Icon_Materials", "Icon_Resources"),
    "Melee": ("Icon_Melee",),
    "Modules": ("Icon_Devices", "Icon_Equipment"),
    "Photovoltaic": ("Icon_Devices", "Icon_Resources"),
    "Pistols": ("Icon_Pistol", "Icon_Weapons"),
    "PoolBall_01": ("Icon_Valuables", "Icon_Resources"),
    "Powerbank": ("Icon_Devices",),
    "Reagent": ("Icon_Reagents", "Icon_Resources"),
    "ReinforcedThread": ("Icon_Textile", "Icon_Fabrics"),
    "RepairKit": ("Icon_Aid_Kits", "Category_Heal"),
    "Rifles": ("Icon_Weapons",),
    "Sealant": ("Icon_Chemicals", "Icon_Resources"),
    "Sensor": ("Icon_Devices",),
    "Shotguns": ("Icon_Shotgun", "Icon_Weapons"),
    "SMG": ("Icon_SMG", "Icon_Weapons"),
    "SniperRifles": ("Icon_SR", "Icon_Weapons"),
    "Tarpaulin": ("Icon_Fabrics", "Icon_Resources"),
    "Termite": ("Icon_ExplosivesParts", "Icon_Resources"),
    "Tools": ("Icon_Tools",),
    "Torpex": ("Icon_ExplosivesParts", "Icon_Resources"),
    "TNT": ("Icon_ExplosivesParts", "Icon_Resources"),
    "Tshirt": ("Icon_Textile",),
    "Uncategorized": ("Icon_All",),
    "USBflash": ("Icon_Devices",),
    "VinylBox": ("Icon_Cases", "Icon_Ware"),
}
CAMO_ICON_ALIASES = ("Icon_BodyMods", "Icon_Tools", "Icon_Resources")
ICON_PRIORITY_DEFAULT = 10
ICON_PRIORITY_SPRITE = 40

ADD_ITEM_EXCLUDED_CATEGORY_IDS = {"12", "18", "23", "26", "27", "28"}
ADD_ITEM_EXCLUDED_VISUAL_CATEGORIES = {"droid", "loot_objects", "lootcontainers"}
VISUAL_CATEGORY_ROOTS_USE_CHILD = {"assets", "prefabs", "items", "weapons", "outfits", "loot"}
VISUAL_CATEGORY_VARIANT_SUFFIXES = ("Small", "Medium", "Large")


def _load_catalog(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _with_builtin_catalog_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = list(rows)
    seen = {(row.get("ItemID") or "").strip() for row in out}
    for row in BUILTIN_CATALOG_ROWS:
        if row["ItemID"] not in seen:
            out.append(dict(row))
            seen.add(row["ItemID"])
    return out


def _normalize_icon_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _icon_tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1
    }


def _visual_path_parts(row: dict[str, str]) -> list[str]:
    raw = (
        row.get("VisualName")
        or row.get("CatalogVisualPath")
        or row.get("IconVisualName")
        or row.get("CatalogIconPath")
        or ""
    )
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    if not parts:
        return []

    lowered = [part.lower() for part in parts]
    if len(parts) >= 4 and lowered[:3] == ["assets", "prefabs", "items"]:
        parts = parts[3:]
        lowered = lowered[3:]
    while len(parts) >= 2 and lowered[0] in {"assets", "prefabs", "items"}:
        parts = parts[1:]
        lowered = lowered[1:]

    return parts


def _clean_visual_category_segment(segment: str) -> str:
    segment = segment.strip()
    if segment.lower().endswith(".prefab"):
        return ""
    return segment


def _normalize_variant_category(category: str) -> str:
    for suffix in VISUAL_CATEGORY_VARIANT_SUFFIXES:
        if category.endswith(suffix) and len(category) > len(suffix):
            return category[: -len(suffix)]
    return category


def _visual_category(row: dict[str, str]) -> str:
    """Return a friendly category derived from VisualName/CatalogVisualPath.

    Examples:
    * ``Weapons/Rifles/...`` -> ``Rifles``
    * ``Ammo/...`` -> ``Ammo``
    * ``Outfits/ArmorVests/...`` -> ``ArmorVests``
    * ``Loot/RepairKitSmall/...`` -> ``RepairKit``

    The final Add Items display may further roll singleton ``Loot/<type>``
    categories back up to ``Loot`` using catalog-wide counts.
    """
    parts = _visual_path_parts(row)
    if not parts:
        return ""

    top = _clean_visual_category_segment(parts[0])
    if not top:
        return ""
    top_lower = top.lower()

    if top_lower in {"weapons", "outfits", "loot"} and len(parts) >= 2:
        child = _clean_visual_category_segment(parts[1])
        if child:
            return _normalize_variant_category(child)

    if top_lower in VISUAL_CATEGORY_ROOTS_USE_CHILD and len(parts) >= 2:
        child = _clean_visual_category_segment(parts[1])
        if child:
            return _normalize_variant_category(child)

    return _normalize_variant_category(top)


def _visual_top_level(row: dict[str, str]) -> str:
    parts = _visual_path_parts(row)
    if not parts:
        return ""
    top = _clean_visual_category_segment(parts[0])
    return top or ""


def _is_datameter_catalog_row(row: dict[str, str]) -> bool:
    item_name = (row.get("ItemName") or "").strip().lower()
    path_blob = " ".join(
        str(row.get(key) or "")
        for key in ("CatalogVisualPath", "CatalogIconPath", "VisualName", "IconVisualName")
    ).lower()
    return item_name == "questtool_01" and "datameter" in path_blob


def _catalog_display_name(row: dict[str, str]) -> str:
    item_name = row.get("ItemName", "")
    if _is_datameter_catalog_row(row):
        return "C-METER / CO-METER (Datameter / QuestTool_01)"
    return item_name


def _catalog_search_aliases(row: dict[str, str]) -> tuple[str, ...]:
    if _is_datameter_catalog_row(row):
        return DATAMETER_SEARCH_ALIASES
    return ()


def _is_addable_catalog_row(row: dict[str, str]) -> bool:
    item_id = (row.get("ItemID") or "").strip()
    item_name = (row.get("ItemName") or "").strip().lower()
    category_id = (row.get("CategoryID") or "").strip()
    visual_category = _visual_category(row).strip().lower()
    # Most Droid-path rows are internal/mod data and make the picker noisy, but
    # servo-named rows are real loot/crafting parts users may need to add.
    allowed_servo_part = visual_category == "droid" and "servo" in item_name
    allowed_hidden_datameter = _is_datameter_catalog_row(row)
    return (
        bool(item_id)
        and (category_id not in ADD_ITEM_EXCLUDED_CATEGORY_IDS or allowed_hidden_datameter)
        and (visual_category not in ADD_ITEM_EXCLUDED_VISUAL_CATEGORIES or allowed_servo_part)
    )



# --- Tooltip management ---
_ALL_TOOLTIPS = []

class ToolTip:
    """Small hover tooltip for Tkinter/ttk widgets."""

    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 450, wraplength: int = 360) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: object = None
        self._auto_hide_id: object = None
        self._tip_window: Optional[tk.Toplevel] = None
        _ALL_TOOLTIPS.append(self)

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<FocusOut>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            with contextlib.suppress(Exception):
                self.widget.after_cancel(self._after_id)
        self._after_id = None
        if self._auto_hide_id is not None:
            with contextlib.suppress(Exception):
                self.widget.after_cancel(self._auto_hide_id)
        self._auto_hide_id = None

    def _show(self) -> None:
        if self._tip_window is not None or not self.text:
            return
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        self._tip_window = tk.Toplevel(self.widget)
        self._tip_window.wm_overrideredirect(True)
        label = tk.Label(
            self._tip_window,
            text=self.text,
            justify="left",
            background="#111827",
            foreground="white",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            wraplength=self.wraplength,
        )
        label.pack()
        self._tip_window.update_idletasks()
        sw = self.widget.winfo_screenwidth()
        sh = self.widget.winfo_screenheight()
        tw = max(self._tip_window.winfo_reqwidth(), 80)
        th = max(self._tip_window.winfo_reqheight(), 20)
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        x = max(0, min(x, sw - tw - 4))
        if y + th > sh - 4:
            y = max(0, self.widget.winfo_rooty() - th - 8)
        self._tip_window.wm_geometry(f"+{x}+{y}")
        self._auto_hide_id = self.widget.after(3000, self._hide)

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


class TreeItemToolTip:
    """Hover tooltip that follows Treeview rows and shows row-specific text."""

    def __init__(self, tree: ttk.Treeview, text_provider, *, delay_ms: int = 350, wraplength: int = 360) -> None:
        self.tree = tree
        self.text_provider = text_provider
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: object = None
        self._auto_hide_id: object = None
        self._tip_window: Optional[tk.Toplevel] = None
        _ALL_TOOLTIPS.append(self)
        self._row_id = ""
        self._event_x_root = 0
        self._event_y_root = 0

        tree.bind("<Motion>", self._on_motion, add="+")
        tree.bind("<Leave>", self._on_leave, add="+")
        tree.bind("<ButtonPress>", self._hide, add="+")
        tree.bind("<MouseWheel>", self._hide, add="+")
        tree.bind("<FocusOut>", self._hide, add="+")
        tree.bind("<Destroy>", self._hide, add="+")

    def _on_motion(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        self._event_x_root = event.x_root
        self._event_y_root = event.y_root
        if row_id == self._row_id:
            return
        self._row_id = row_id
        self._hide()
        if not row_id:
            return
        text = self.text_provider(row_id)
        if not text:
            return
        self._after_id = self.tree.after(self.delay_ms, lambda: self._show(text))

    def _cancel(self) -> None:
        if self._after_id is not None:
            with contextlib.suppress(Exception):
                self.tree.after_cancel(self._after_id)
        self._after_id = None
        if self._auto_hide_id is not None:
            with contextlib.suppress(Exception):
                self.tree.after_cancel(self._auto_hide_id)
        self._auto_hide_id = None

    def _show(self, text: str) -> None:
        self._cancel()
        if self._tip_window is not None or not text:
            return
        if not self.tree.winfo_exists() or not self.tree.winfo_viewable():
            return
        self._tip_window = tk.Toplevel(self.tree)
        self._tip_window.wm_overrideredirect(True)
        label = tk.Label(
            self._tip_window,
            text=text,
            justify="left",
            background="#111827",
            foreground="white",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            wraplength=self.wraplength,
        )
        label.pack()
        self._tip_window.update_idletasks()
        sw = self.tree.winfo_screenwidth()
        sh = self.tree.winfo_screenheight()
        tw = max(self._tip_window.winfo_reqwidth(), 80)
        th = max(self._tip_window.winfo_reqheight(), 20)
        x = max(0, min(self._event_x_root + 16, sw - tw - 4))
        y = self._event_y_root + 18
        if y + th > sh - 4:
            y = max(0, self._event_y_root - th - 12)
        self._tip_window.wm_geometry(f"+{x}+{y}")
        self._auto_hide_id = self.tree.after(3000, self._hide)

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None

    def _on_leave(self, _event: object = None) -> None:
        self._row_id = ""
        self._hide()


class EditorGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cargo Hunters Save Editor")
        # Start maximized (Windows: 'zoomed', fallback: geometry)
        try:
            self.state('zoomed')
        except Exception:
            self.geometry("1280x760")
        self.minsize(1050, 650)

        self.catalog: list[dict[str, str]] = []
        self.catalog_by_template: dict[str, dict[str, str]] = {}
        self.visual_category_by_template: dict[str, str] = {}
        self.stack_max_by_template: dict[str, int] = {}
        self.filtered: list[dict[str, str]] = []
        self.containers: list[Container] = []
        self.add_item_refs: dict[str, dict[str, str]] = {}
        self.add_category_refs: dict[str, str] = {}
        self.add_category_open_state: dict[str, bool] = {}
        self.inventory_item_refs: dict[str, tuple[str, str, str]] = {}
        self.inventory_repair_refs: dict[str, tuple[str, str, str]] = {}
        self.inventory_container_refs: dict[str, str] = {}
        self.inventory_container_open_state: dict[str, bool] = {}
        self.inventory_filter_index: dict[str, str] = {}
        self._inventory_data: Optional[dict] = None
        self._sort_reverse: dict[tuple[str, str], bool] = {}
        self._sort_state: dict[str, tuple[str, bool]] = {}
        self.active_view = "add"
        self._auto_qty_template: Optional[str] = None
        self.icon_exact: dict[str, Path] = {}
        self.icon_exact_priority: dict[str, int] = {}
        self.icon_candidates: list[tuple[Path, set[str], str, str, int]] = []
        self.icon_candidate_paths: set[Path] = set()
        self.icon_match_cache: dict[tuple[str, str], Optional[Path]] = {}
        self.icon_name_match_cache: dict[str, Optional[Path]] = {}
        self.photo_cache: dict[tuple[Path, int], tk.PhotoImage] = {}
        self.selected_icon_photo: Optional[tk.PhotoImage] = None
        self.catalog_by_name_compact: dict[str, dict[str, str]] = {}
        self.group_add_categories_var = tk.BooleanVar(value=True)
        self.group_inventory_containers_var = tk.BooleanVar(value=True)
        self.inventory_view_mode_var = tk.StringVar(value=INVENTORY_VIEW_MODES[0])
        self.inventory_weapons_first_var = tk.BooleanVar(value=False)

        self._build_widgets()
        self._load_icons()
        self._load_settings()
        self._load_default_catalog()
        self._refresh_containers()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", lambda e: self._hide_all_tooltips())  # Minimized
        self.bind("<FocusOut>", lambda e: self._hide_all_tooltips())
        self.bind("<Configure>", lambda e: self._hide_all_tooltips() if self.state() == 'iconic' else None)

    def _hide_all_tooltips(self):
        for tip in _ALL_TOOLTIPS:
            tip._hide()

    # ---------- UI ----------
    def _tooltip(self, widget: tk.Widget, text: str) -> tk.Widget:
        ToolTip(widget, text)
        return widget

    def _show_quick_start(self) -> None:
        messagebox.showinfo(
            "Cargo Hunters Save Editor - quick start",
            "Recommended workflow:\n\n"
            "1. Close Cargo Hunters.\n"
            "2. Confirm the Save file path points to offline.save.\n"
            "3. Confirm the Item catalog CSV is loaded.\n"
            "4. Choose an Add destination container.\n\n"
            "Add new items:\n"
            "• Open Add new items.\n"
            "• Search/filter the catalog.\n"
            "• Use Group by categories to switch between grouped browsing and a flat sortable list.\n"
            "• Select one item row, or Ctrl/Shift-click multiple rows, adjust quantity/count if needed, then click Add.\n\n"
            "Edit current inventory:\n"
            "• Open Edit current inventory.\n"
            "• Select item rows to split, repair/refill, top off, or delete.\n"
            "• Shelter storage is hidden in this view by design.\n\n"
            "Every save-changing action creates a timestamped backup.",
        )

    def _build_widgets(self) -> None:
        self.style = ttk.Style(self)
        self.style.configure("Treeview", rowheight=24)

        top = ttk.LabelFrame(self, text="Setup - verify these before editing", padding=8)
        top.pack(fill="x")

        self._tooltip(
            ttk.Label(top, text="1. Save file (offline.save):"),
            "The save file that will be read and modified. Close Cargo Hunters before editing this file.",
        ).grid(row=0, column=0, sticky="w")
        self.save_var = tk.StringVar(value=str(DEFAULT_SAVE))
        save_entry = self._tooltip(
            ttk.Entry(top, textvariable=self.save_var, width=80),
            "Path to the Cargo Hunters offline.save file. Defaults to your LocalLow save folder.",
        )
        save_entry.grid(row=0, column=1, sticky="we", padx=4)
        browse_button = self._tooltip(
            ttk.Button(top, text="Browse save…", command=self._pick_save),
            "Choose a different offline.save file and rescan its containers.",
        )
        browse_button.grid(row=0, column=2)

        backup_keep_frame = ttk.Frame(top)
        backup_keep_frame.grid(row=0, column=3, columnspan=2, sticky="e", padx=(8, 0))
        self._tooltip(
            ttk.Label(backup_keep_frame, text="Keep last"),
            "How many timestamped .bak files to keep next to offline.save. Older backups beyond this count are pruned after each save.",
        ).pack(side="left")
        self.backup_keep_var = tk.IntVar(value=20)
        self.backup_keep_spin = self._tooltip(
            ttk.Spinbox(
                backup_keep_frame,
                from_=1,
                to=500,
                width=5,
                textvariable=self.backup_keep_var,
                justify="right",
            ),
            "Maximum number of .bak files to keep (1–500). Older backups are deleted after each save. The most recent backup is always preserved.",
        )
        self.backup_keep_spin.pack(side="left", padx=(4, 4))
        self._tooltip(
            ttk.Label(backup_keep_frame, text="backups"),
            "How many timestamped .bak files to keep next to offline.save.",
        ).pack(side="left")
        prune_now_button = self._tooltip(
            ttk.Button(backup_keep_frame, text="Prune now", command=self._prune_backups_now),
            "Delete .bak files for the current save beyond the Keep-last count, without writing a new save.",
        )
        prune_now_button.pack(side="left", padx=(8, 0))

        self._tooltip(
            ttk.Label(top, text="2. Item catalog CSV:"),
            "The catalog used to populate item names, categories, stack capacities, dimensions, and search fields.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.csv_var = tk.StringVar(value=str(DEFAULT_CSV))
        csv_entry = self._tooltip(
            ttk.Entry(top, textvariable=self.csv_var, width=80),
            "Item catalog CSV used for names, categories, sizes, stack capacities, and addable item templates.",
        )
        csv_entry.grid(row=1, column=1, sticky="we", padx=4, pady=(4, 0))
        reload_button = self._tooltip(
            ttk.Button(top, text="Reload CSV", command=self._reload_catalog_and_views),
            "Reload the item catalog CSV, rebuild the Add Items list, and rescan save containers.",
        )
        reload_button.grid(row=1, column=2, pady=(4, 0))
        reload_icons_button = self._tooltip(
            ttk.Button(top, text="Reload icons", command=self._reload_icons_and_views),
            "Reload exported UI icon PNGs and refresh icon previews in both views.",
        )
        reload_icons_button.grid(row=1, column=3, padx=(4, 0), pady=(4, 0))
        if not IS_FROZEN:
            pull_csv_button = self._tooltip(
                ttk.Button(top, text="Pull new CSV from game…", command=self._pull_new_csv_from_game),
                "Extract a fresh item catalog from a Cargo Hunters install folder, back up the current CSV, replace it, then reload the editor.",
            )
            pull_csv_button.grid(row=1, column=4, padx=(4, 0), pady=(4, 0))

        self._tooltip(
            ttk.Label(top, text="3. Add destination:"),
            "The inventory/equipment/shelter container where newly added items will be placed.",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.dest_var = tk.StringVar()
        self.dest_combo = ttk.Combobox(top, textvariable=self.dest_var, state="readonly")
        self._tooltip(
            self.dest_combo,
            "Destination container for newly added items. Changing it updates the suggested grid width.",
        )
        self.dest_combo.grid(row=2, column=1, sticky="we", padx=4, pady=(4, 0))
        self.dest_combo.bind("<<ComboboxSelected>>", self._on_dest_changed)
        rescan_button = self._tooltip(
            ttk.Button(top, text="Rescan containers", command=self._refresh_containers),
            "Rescan the selected save file for inventory, equipment, and shelter containers.",
        )
        rescan_button.grid(row=2, column=2, pady=(4, 0))

        top.columnconfigure(1, weight=1)

        help_bar = tk.Frame(self, bg=VIEW_THEME["add"]["panel"], padx=10, pady=6)
        help_bar.pack(fill="x", padx=8, pady=(0, 6))
        self.help_bar = help_bar
        self.help_bar_label = tk.Label(
            help_bar,
            text="Start here: confirm the save file, reload/pull the catalog if needed, choose an Add destination, then pick a task below. Backups are automatic.",
            bg=VIEW_THEME["add"]["panel"],
            fg=VIEW_THEME["add"]["fg"],
            anchor="w",
        )
        self.help_bar_label.pack(side="left", fill="x", expand=True)
        quick_start_button = self._tooltip(
            ttk.Button(help_bar, text="Quick start help", command=self._show_quick_start),
            "Open a short walkthrough explaining the two main workflows.",
        )
        quick_start_button.pack(side="right")

        view_bar = ttk.Frame(self, padding=(8, 0, 8, 4))
        view_bar.pack(fill="x")
        self._tooltip(
            ttk.Label(view_bar, text="Choose a task:", font=("Segoe UI", 10, "bold")),
            "Switch between adding new catalog items and editing items already in your save.",
        ).pack(side="left", padx=(0, 8))
        self.add_view_button = tk.Button(
            view_bar,
            text="Add new items",
            command=lambda: self._show_view("add"),
            padx=18,
            pady=6,
            relief="sunken",
        )
        self._tooltip(self.add_view_button, "Switch to the Add Items screen for inserting new item templates into a container.")
        self.add_view_button.pack(side="left")
        self.inventory_view_button = tk.Button(
            view_bar,
            text="Edit current inventory",
            command=lambda: self._show_view("inventory"),
            padx=18,
            pady=6,
            relief="raised",
        )
        self._tooltip(self.inventory_view_button, "Switch to the Current Inventory screen to inspect, split, repair, top off, or delete existing items.")
        self.inventory_view_button.pack(side="left", padx=(6, 0))
        self.character_view_button = tk.Button(
            view_bar,
            text="Edit character",
            command=lambda: self._show_view("character"),
            padx=18,
            pady=6,
            relief="raised",
        )
        self._tooltip(self.character_view_button, "Switch to the Character screen to view and edit nickname, level, XP, and skill points.")
        self.character_view_button.pack(side="left", padx=(6, 0))

        self.view_container = tk.Frame(self, bg=VIEW_THEME["add"]["bg"])
        self.view_container.pack(fill="both", expand=True, padx=8, pady=4)

        add_tab = ttk.Frame(self.view_container)
        inventory_tab = ttk.Frame(self.view_container)
        character_tab = ttk.Frame(self.view_container)
        self.add_view = add_tab
        self.inventory_view = inventory_tab
        self.character_view = character_tab

        body = ttk.Panedwindow(add_tab, orient="horizontal")
        body.pack(fill="both", expand=True)

        # --- Left: search + treeview ---
        left = ttk.Frame(body)
        body.add(left, weight=3)

        self._tooltip(
            ttk.Label(left, text="Step 1: Find an item to add", font=("Segoe UI", 10, "bold")),
            "Filter, sort, hover, and select one or more item rows to add.",
        ).pack(anchor="w")
        self._tooltip(ttk.Label(
            left,
            text="Expand a category, filter the catalog, then select one or more item rows. Category header rows are only for grouping.",
            foreground="#555",
            wraplength=760,
        ), "Ctrl/Shift-click item rows for multi-select. Turn off Group by categories for a flat list that sorts globally.").pack(anchor="w", pady=(0, 4))

        search_row = ttk.Frame(left)
        search_row.pack(fill="x")
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search catalog:", width=16, anchor="e").grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.bind("<Return>", lambda _e: self._apply_filter())
        search_entry.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(search_row, text="Search", command=self._apply_filter).grid(row=0, column=2, sticky="e", padx=(4, 0))
        def _clear_catalog_search():
            self.search_var.set("")
            self._apply_filter()
        ttk.Button(search_row, text="Clear", command=_clear_catalog_search).grid(row=0, column=3, sticky="e", padx=(2, 0))
        collapse_add_button = self._tooltip(
            ttk.Button(search_row, text="Collapse all", command=self._collapse_all_add_categories),
            "Collapse every category group in the Add Items list. The collapsed state is remembered between refreshes.",
        )
        collapse_add_button.grid(row=0, column=4, sticky="e", padx=(6, 0))
        expand_add_button = self._tooltip(
            ttk.Button(search_row, text="Expand all", command=self._expand_all_add_categories),
            "Expand every category group in the Add Items list. The expanded state is remembered between refreshes.",
        )
        expand_add_button.grid(row=0, column=5, sticky="e", padx=(4, 0))
        group_add_check = self._tooltip(
            ttk.Checkbutton(
                search_row,
                text="Group by categories",
                variable=self.group_add_categories_var,
                command=self._apply_filter,
            ),
            "Turn category grouping on or off. When off, all matching items appear in one flat sortable list.",
        )
        group_add_check.grid(row=0, column=6, sticky="e", padx=(10, 0))

        cols = ("name", "id", "visual_category", "category", "subcat", "price", "weight", "size")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings", selectmode="extended")
        self.tree.tag_configure("cat_blue", foreground="#3b82f6")
        self.tree.tag_configure("cat_green", foreground="#16a34a")
        self.tree.tag_configure("cat_red", foreground="#dc2626")
        self.tree.tag_configure("cat_orange", foreground="#c2410c")
        self.tree.tag_configure("cat_teal", foreground="#0e7490")
        self.tree.tag_configure("cat_gold", foreground="#ca8a04")
        self.tree.tag_configure("cat_purple", foreground="#7c3aed")
        self.tree.tag_configure("cat_pink", foreground="#be185d")
        self.tree.tag_configure("cat_gray", foreground="#4b5563")
        self.tree.heading("#0", text="Category", command=lambda: self._sort_treeview(self.tree, "#0"))
        self.tree.column("#0", width=170, minwidth=130, stretch=False, anchor="w")
        widths = {"name": 230, "id": 245, "visual_category": 95, "category": 60, "subcat": 60, "price": 70, "weight": 60, "size": 60}
        headings = {
            "name": "Name",
            "id": "TemplateId",
            "visual_category": "Type",
            "category": "Cat ID",
            "subcat": "Sub",
            "price": "Base $",
            "weight": "Wt",
            "size": "WxH",
        }
        for c in cols:
            self.tree.heading(c, text=headings[c], command=lambda col=c: self._sort_treeview(self.tree, col))
            self.tree.column(c, width=widths[c], anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(4, 0))
        TreeItemToolTip(self.tree, self._add_tree_hover_text)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_add_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_add_tree_close)

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        ysb.place(in_=self.tree, relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # --- Right: details + add controls + log ---
        right = ttk.Frame(body)
        body.add(right, weight=2)

        details = ttk.LabelFrame(right, text="Step 2: Confirm selected item", padding=6)
        details.pack(fill="x")
        self.sel_name_var = tk.StringVar(value="(none)")
        self.sel_id_var = tk.StringVar(value="")
        details_body = ttk.Frame(details)
        details_body.pack(fill="x")
        self.icon_preview_label = ttk.Label(details_body, text="No icon\nloaded", width=14, anchor="center")
        self._tooltip(self.icon_preview_label, "Preview of the best-matched exported UI icon for the selected item.")
        self.icon_preview_label.pack(side="left", padx=(0, 8))
        details_text = ttk.Frame(details_body)
        details_text.pack(side="left", fill="x", expand=True)
        ttk.Label(details_text, textvariable=self.sel_name_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(details_text, textvariable=self.sel_id_var, foreground="#555").pack(anchor="w")

        opts = ttk.LabelFrame(right, text="Step 3: Optional add settings", padding=6)
        opts.pack(fill="x", pady=(6, 0))

        ttk.Label(
            opts,
            text="Most fields can stay blank. Stack quantity auto-fills for known stackable items.",
            foreground="#555",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.qty_var = tk.StringVar()
        self.count_var = tk.StringVar(value="1")
        self.cond_var = tk.StringVar()
        self.dur_var = tk.StringVar()
        self.grid_var = tk.StringVar(value="10")

        for r, (label, var, hint) in enumerate([
            ("Quantity (stackables)", self.qty_var, "blank = not stackable"),
            ("Count (instances)", self.count_var, "how many separate items"),
            ("Condition", self.cond_var, "e.g. 1.0 for pristine"),
            ("Durability", self.dur_var, "e.g. 1.0 for pristine"),
            ("Backpack grid width", self.grid_var, "blank = use container's own width or 10"),
        ], start=1):
            tooltip_text = {
                "Quantity (stackables)": "Stack quantity to place on each added stackable item. Leave blank for non-stackable items; selecting known stackables auto-fills their max stack.",
                "Count (instances)": "Number of separate item instances to add. For ammo/cash stacks, keep this at 1 unless you want multiple stacks.",
                "Condition": f"Optional condition value for newly added items. Full repair uses {CONDITION_FULL_VALUE}; leave blank to omit the stat.",
                "Durability": "Optional durability/use value for newly added items. Leave blank to omit the stat.",
                "Backpack grid width": "Grid width used when finding a free slot in the selected destination. Usually auto-filled from the selected container.",
            }[label]
            self._tooltip(ttk.Label(opts, text=label), tooltip_text).grid(row=r, column=0, sticky="w", pady=2)
            entry = self._tooltip(
                ttk.Entry(opts, textvariable=var, width=12),
                tooltip_text,
            )
            entry.grid(row=r, column=1, sticky="w", padx=4)
            hint_label = self._tooltip(ttk.Label(opts, text=hint, foreground="#777"), f"Hint for {label}.")
            hint_label.grid(row=r, column=2, sticky="w")

        add_button = self._tooltip(
            ttk.Button(right, text="Step 4: Add selected item(s) to destination", command=self._do_add),
            "Add all selected item rows to the selected destination container. Ctrl/Shift-click to select multiple items. A timestamped backup is created before each save change.",
        )
        add_button.pack(fill="x", pady=8)

        log_frame = ttk.LabelFrame(right, text="Log", padding=4)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=12, wrap="word")
        self._tooltip(self.log, "Action log showing backups, added items, repair/top-off statistics, split results, and icon/catalog reload messages.")
        self.log.pack(fill="both", expand=True)

        # --- Current inventory tab ---
        ttk.Label(
            inventory_tab,
            text="Edit current inventory - shelter storage is hidden here",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            inventory_tab,
            text="Use this screen to inspect carried/equipped items. Select item rows first, then choose an action below.",
            foreground="#555",
            wraplength=1100,
        ).pack(anchor="w", pady=(0, 4))

        inv_toolbar = ttk.LabelFrame(inventory_tab, text="View", padding=(6, 4, 6, 6))
        inv_toolbar.pack(fill="x", pady=(0, 4))

        ttk.Label(inv_toolbar, text="Show:").pack(side="left")
        self.inv_view_mode_combo = ttk.Combobox(
            inv_toolbar,
            textvariable=self.inventory_view_mode_var,
            state="readonly",
            width=12,
            values=list(INVENTORY_VIEW_MODES),
        )
        self._tooltip(
            self.inv_view_mode_combo,
            "Equipment: show backpacks, vests, rigs, holsters and the items they hold (current grouped view, including empty containers).\n"
            "Items: show a flat list of just the carried items (weapons, ammo, valuables, consumables) without container header rows.",
        )
        self.inv_view_mode_combo.pack(side="left", padx=(4, 8))
        self.inv_view_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_inventory_view())

        ttk.Label(inv_toolbar, text="Container:").pack(side="left")
        self.inv_container_var = tk.StringVar(value="All containers")
        self.inv_container_combo = ttk.Combobox(inv_toolbar, textvariable=self.inv_container_var, state="readonly", width=52)
        self._tooltip(
            self.inv_container_combo,
            "Choose which inventory/equipment container to display, or keep All containers to inspect all non-shelter current inventory containers.",
        )
        self.inv_container_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.inv_container_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_inventory_view())
        refresh_inventory_button = self._tooltip(
            ttk.Button(inv_toolbar, text="Refresh", command=self._refresh_inventory_view),
            "Reload the Current Inventory table from the selected save file while preserving sort and collapse state.",
        )
        refresh_inventory_button.pack(side="left", padx=(4, 0))
        inv_action_row = ttk.LabelFrame(inventory_tab, text="Actions", padding=(6, 4, 6, 6))
        inv_action_row.pack(fill="x", pady=(0, 4))
        ttk.Label(inv_action_row, text="Selected rows:").pack(side="left")
        split_stack_button = self._tooltip(
            ttk.Button(inv_action_row, text="Split one stack", command=self._split_selected_inventory_stack),
            "Split one selected stackable item into two stacks using a slider. Requires a selected item row with a stored stack quantity.",
        )
        split_stack_button.pack(side="left", padx=(4, 0))
        repair_selected_button = self._tooltip(
            ttk.Button(inv_action_row, text="Repair/refill selected", command=self._repair_selected_inventory_items),
            "Set selected item rows to 100% condition/durability, refill known uses, and top off known stack sizes. Creates a backup if anything changes.",
        )
        repair_selected_button.pack(side="left", padx=(4, 0))
        ttk.Label(inv_action_row, text="All visible sources:").pack(side="left", padx=(14, 0))
        repair_all_button = self._tooltip(
            ttk.Button(inv_action_row, text="Repair/refill/top off ALL", command=self._repair_all_inventory_items),
            "Scan all non-shelter inventory/equipment items, then repair/refill/top off everything with known max values. Creates a backup if anything changes.",
        )
        repair_all_button.pack(side="left", padx=(4, 0))
        ttk.Label(inv_action_row, text="Danger zone:").pack(side="left", padx=(14, 0))
        move_selected_button = self._tooltip(
            ttk.Button(inv_action_row, text="Move selected\u2026", command=self._move_selected_inventory_items),
            "Move selected items into a chosen container. Containers carry their contents with them. Checks the destination grid has enough free slots; warns if not.",
        )
        move_selected_button.pack(side="left", padx=(4, 0))
        delete_selected_button = self._tooltip(
            ttk.Button(inv_action_row, text="Delete selected", command=self._delete_selected_inventory_items),
            "Delete selected item rows from the save. Container header rows are ignored. A timestamped backup is created first.",
        )
        delete_selected_button.pack(side="left", padx=(4, 0))

        inv_filter_row = ttk.Frame(inventory_tab, padding=(0, 0, 0, 6))
        inv_filter_row.pack(fill="x")
        inv_filter_row.columnconfigure(1, weight=1)
        ttk.Label(inv_filter_row, text="Search inventory:", width=16, anchor="e").grid(row=0, column=0, sticky="w")
        self.inventory_filter_var = tk.StringVar()
        inventory_filter_entry = ttk.Entry(inv_filter_row, textvariable=self.inventory_filter_var)
        inventory_filter_entry.bind("<Return>", lambda _e: self._refresh_inventory_view())
        inventory_filter_entry.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(inv_filter_row, text="Search", command=self._refresh_inventory_view).grid(row=0, column=2, sticky="e", padx=(4, 0))
        def _clear_inventory_search():
            self.inventory_filter_var.set("")
            self._refresh_inventory_view()
        ttk.Button(inv_filter_row, text="Clear", command=_clear_inventory_search).grid(row=0, column=3, sticky="e", padx=(2, 0))
        collapse_inventory_button = self._tooltip(
            ttk.Button(inv_filter_row, text="Collapse all", command=self._collapse_all_inventory_containers),
            "Collapse all visible inventory container groups. The collapsed state is remembered between actions.",
        )
        collapse_inventory_button.grid(row=0, column=4, sticky="e", padx=(6, 0))
        expand_inventory_button = self._tooltip(
            ttk.Button(inv_filter_row, text="Expand all", command=self._expand_all_inventory_containers),
            "Expand all visible inventory container groups. The expanded state is remembered between actions.",
        )
        expand_inventory_button.grid(row=0, column=5, sticky="e", padx=(4, 0))
        group_inventory_check = self._tooltip(
            ttk.Checkbutton(
                inv_filter_row,
                text="Group by containers",
                variable=self.group_inventory_containers_var,
                command=self._refresh_inventory_view,
            ),
            "Turn Current Inventory grouping on or off. When off, all visible items appear in one flat sortable list without container header rows.",
        )
        group_inventory_check.grid(row=0, column=6, sticky="e", padx=(10, 0))
        weapons_first_check = self._tooltip(
            ttk.Checkbutton(
                inv_filter_row,
                text="Expandable on top",
                variable=self.inventory_weapons_first_var,
                command=self._refresh_inventory_view,
            ),
            "In Items mode, list expandable rows (weapons with their attached parts) at the top of the inventory, before non-expandable items. Has no effect in Equipment mode.",
        )
        weapons_first_check.grid(row=0, column=7, sticky="e", padx=(10, 0))

        inv_cols = ("qty", "pos", "size", "type", "category", "condition", "durability", "source", "id", "template")
        self.inventory_tree = ttk.Treeview(
            inventory_tab,
            columns=inv_cols,
            show="tree headings",
            selectmode="extended",
        )
        self.inventory_tree.tag_configure("cat_blue", foreground="#3b82f6")
        self.inventory_tree.tag_configure("cat_green", foreground="#16a34a")
        self.inventory_tree.tag_configure("cat_red", foreground="#dc2626")
        self.inventory_tree.tag_configure("cat_orange", foreground="#c2410c")
        self.inventory_tree.tag_configure("cat_teal", foreground="#0e7490")
        self.inventory_tree.tag_configure("cat_gold", foreground="#ca8a04")
        self.inventory_tree.tag_configure("cat_purple", foreground="#7c3aed")
        self.inventory_tree.tag_configure("cat_pink", foreground="#be185d")
        self.inventory_tree.tag_configure("cat_gray", foreground="#4b5563")
        self.inventory_tree.heading("#0", text="Item / container", command=lambda: self._sort_treeview(self.inventory_tree, "#0"))
        self.inventory_tree.column("#0", width=300, anchor="w")
        inv_headings = {
            "qty": "Qty",
            "pos": "Pos",
            "size": "WxH",
            "type": "Type",
            "category": "Cat",
            "condition": "Cond",
            "durability": "Dur",
            "source": "Source",
            "id": "Item Id",
            "template": "TemplateId",
        }
        inv_widths = {
            "qty": 70,
            "pos": 70,
            "size": 55,
            "type": 110,
            "category": 50,
            "condition": 135,
            "durability": 95,
            "source": 80,
            "id": 245,
            "template": 245,
        }
        for c in inv_cols:
            self.inventory_tree.heading(c, text=inv_headings[c], command=lambda col=c: self._sort_treeview(self.inventory_tree, col))
            self.inventory_tree.column(c, width=inv_widths[c], anchor="w")
        self.inventory_tree.pack(fill="both", expand=True)
        TreeItemToolTip(self.inventory_tree, self._inventory_tree_hover_text)
        self.inventory_tree.bind("<<TreeviewOpen>>", self._on_inventory_tree_open)
        self.inventory_tree.bind("<<TreeviewClose>>", self._on_inventory_tree_close)
        self.inventory_tree.bind("<Delete>", self._on_inventory_delete_key)
        self.inventory_tree.bind("<KP_Delete>", self._on_inventory_delete_key)

        inv_ysb = ttk.Scrollbar(inventory_tab, orient="vertical", command=self.inventory_tree.yview)
        self.inventory_tree.configure(yscrollcommand=inv_ysb.set)
        inv_ysb.place(in_=self.inventory_tree, relx=1.0, rely=0, relheight=1.0, anchor="ne")

        self._build_character_view(character_tab)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x")
        self._show_view("add")

    def _apply_view_theme(self, view: str) -> None:
        theme = VIEW_THEME[view]
        self.configure(bg=theme["bg"])
        self.style.configure("TFrame", background=theme["bg"])
        self.style.configure("TLabelframe", background=theme["bg"])
        self.style.configure("TLabelframe.Label", background=theme["bg"], foreground=theme["fg"])
        self.style.configure("TLabel", background=theme["bg"])
        if hasattr(self, "view_container"):
            self.view_container.configure(bg=theme["bg"])
        if hasattr(self, "help_bar"):
            self.help_bar.configure(bg=theme["panel"])
        if hasattr(self, "help_bar_label"):
            self.help_bar_label.configure(bg=theme["panel"], fg=theme["fg"])

    def _show_view(self, view: str) -> None:
        self.active_view = view
        self._apply_view_theme(view)
        for frame in (
            getattr(self, "add_view", None),
            getattr(self, "inventory_view", None),
            getattr(self, "character_view", None),
        ):
            if frame is not None:
                frame.pack_forget()
        active_theme = VIEW_THEME[view]
        selected = {"bg": active_theme["button"], "fg": "white", "relief": "sunken", "activebackground": active_theme["button_active"], "activeforeground": "white"}
        unselected = {"bg": "#e5e7eb", "fg": "#111827", "relief": "raised", "activebackground": "#d1d5db", "activeforeground": "#111827"}
        self.add_view_button.configure(**(selected if view == "add" else unselected))
        self.inventory_view_button.configure(**(selected if view == "inventory" else unselected))
        if hasattr(self, "character_view_button"):
            self.character_view_button.configure(**(selected if view == "character" else unselected))
        if view == "inventory":
            self.inventory_view.pack(fill="both", expand=True)
            self._refresh_inventory_view()
        elif view == "character":
            self.character_view.pack(fill="both", expand=True)
            self._refresh_character_view()
        else:
            self.add_view.pack(fill="both", expand=True)

    # ---------- character view ----------
    def _build_character_view(self, parent: ttk.Frame) -> None:
        """Build the read/edit Character tab. Inventory data is untouched here."""
        self.character_nickname_var = tk.StringVar(value="")
        self.character_level_var = tk.StringVar(value="")
        self.character_xp_var = tk.StringVar(value="")
        self.character_next_goal_var = tk.StringVar(value="")
        self.character_skill_points_var = tk.StringVar(value="")
        self.character_account_id_var = tk.StringVar(value="-")
        self.character_auth_chip_var = tk.StringVar(value="-")
        self._character_loaded = False
        # Pending per-skill edits: {skill_id: {"Level": int|None, "NextLevelExperienceGoal": int|None}}
        # Populated by `_edit_selected_skill`, consumed by `_apply_character_changes`,
        # cleared by `_refresh_character_view`.
        self._pending_skill_changes: dict[int, dict[str, Optional[int]]] = {}
        # Maps a skills_tree row id -> int skill id, so we can look skills up
        # again after the user sorts the table.
        self._character_skill_row_ids: dict[str, int] = {}
        # Source-of-truth current values per skill id, so editing two columns
        # in sequence shows a coherent diff against the save.
        self._character_skill_current: dict[int, dict[str, int]] = {}

        outer = ttk.Frame(parent, padding=(8, 6, 8, 6))
        outer.pack(fill="both", expand=True)

        # ---- Identity ----
        identity = ttk.LabelFrame(outer, text="Identity", padding=(8, 6, 8, 6))
        identity.pack(fill="x")
        ttk.Label(identity, text="Nickname:").grid(row=0, column=0, sticky="w")
        nick_entry = ttk.Entry(identity, textvariable=self.character_nickname_var, width=28)
        nick_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        self._tooltip(nick_entry, "In-game display name shown on profile, leaderboards, and elsewhere.")

        ttk.Label(identity, text="Account ID:").grid(row=0, column=2, sticky="w")
        ttk.Label(identity, textvariable=self.character_account_id_var, foreground="#555").grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(identity, text="Auth Chip:").grid(row=0, column=4, sticky="w")
        ttk.Label(identity, textvariable=self.character_auth_chip_var, foreground="#555").grid(row=0, column=5, sticky="w", padx=(6, 0))

        # ---- Experience & skill points ----
        progression = ttk.LabelFrame(outer, text="Progression", padding=(8, 6, 8, 6))
        progression.pack(fill="x", pady=(8, 0))
        ttk.Label(progression, text="Level:").grid(row=0, column=0, sticky="w")
        level_entry = ttk.Entry(progression, textvariable=self.character_level_var, width=8)
        level_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        self._tooltip(level_entry, "Account level (integer). Saved as AccountDto.ExperienceDto.Level.")

        ttk.Label(progression, text="XP:").grid(row=0, column=2, sticky="w")
        xp_entry = ttk.Entry(progression, textvariable=self.character_xp_var, width=14)
        xp_entry.grid(row=0, column=3, sticky="w", padx=(6, 18))
        self._tooltip(xp_entry, "Current experience points (integer). Saved as AccountDto.ExperienceDto.ExperiencePoints.")

        ttk.Label(progression, text="Next-level goal:").grid(row=0, column=4, sticky="w")
        goal_entry = ttk.Entry(progression, textvariable=self.character_next_goal_var, width=14)
        goal_entry.grid(row=0, column=5, sticky="w", padx=(6, 18))
        self._tooltip(goal_entry, "XP required for the next level. Saved as AccountDto.ExperienceDto.NextLevelExperienceGoal.")

        ttk.Label(progression, text="Unspent skill points:").grid(row=1, column=0, sticky="w", pady=(6, 0), columnspan=2)
        sp_entry = ttk.Entry(progression, textvariable=self.character_skill_points_var, width=8)
        sp_entry.grid(row=1, column=2, sticky="w", padx=(6, 18), pady=(6, 0))
        self._tooltip(sp_entry, "Available skill points to spend (integer). Saved as AccountDto.SkillsDto.SkillPointsCount.")

        action_bar = ttk.Frame(outer, padding=(0, 8, 0, 0))
        action_bar.pack(fill="x")
        self._tooltip(
            ttk.Button(action_bar, text="Reload from save", command=self._refresh_character_view),
            "Discard any unsaved edits in this tab and reload values from the save file.",
        ).pack(side="left")
        self._tooltip(
            ttk.Button(action_bar, text="Apply character changes", command=self._apply_character_changes),
            "Validate the editable fields, write a backup, and update the save with the new nickname, level, XP, next goal, and skill points.",
        ).pack(side="left", padx=(8, 0))

        # ---- Read-only tables: skills + counters in a paned window ----
        tables = ttk.Panedwindow(outer, orient="horizontal")
        tables.pack(fill="both", expand=True, pady=(10, 0))

        skills_frame = ttk.LabelFrame(tables, text="Skills (double-click a row to edit)", padding=(4, 4, 4, 4))
        tables.add(skills_frame, weight=1)
        skills_cols = ("id", "level", "next_goal")
        self.character_skills_tree = ttk.Treeview(skills_frame, columns=skills_cols, show="headings", selectmode="browse")
        for col, heading, width, anchor in (
            ("id", "Skill Id", 80, "w"),
            ("level", "Level", 70, "e"),
            ("next_goal", "Next-level goal", 130, "e"),
        ):
            self.character_skills_tree.heading(col, text=heading, command=lambda c=col: self._sort_treeview(self.character_skills_tree, c))
            self.character_skills_tree.column(col, width=width, anchor=anchor, stretch=True)
        self.character_skills_tree.tag_configure("pending", background="#fef9c3", foreground="#854d0e")
        self.character_skills_tree.bind("<Double-1>", self._on_character_skill_double_click)
        self.character_skills_tree.bind("<Return>", lambda _e: self._edit_selected_skill())
        self.character_skills_tree.pack(fill="both", expand=True, side="left")
        skills_ysb = ttk.Scrollbar(skills_frame, orient="vertical", command=self.character_skills_tree.yview)
        self.character_skills_tree.configure(yscrollcommand=skills_ysb.set)
        skills_ysb.pack(side="right", fill="y")

        counters_frame = ttk.LabelFrame(tables, text="Lifetime counters (read-only)", padding=(4, 4, 4, 4))
        tables.add(counters_frame, weight=2)
        counters_cols = ("group", "stat", "value", "last_set")
        self.character_counters_tree = ttk.Treeview(counters_frame, columns=counters_cols, show="headings", selectmode="browse")
        for col, heading, width, anchor in (
            ("group", "Group ($t)", 100, "w"),
            ("stat", "Stat", 200, "w"),
            ("value", "Value", 110, "e"),
            ("last_set", "Last set (UTC)", 170, "w"),
        ):
            self.character_counters_tree.heading(col, text=heading, command=lambda c=col: self._sort_treeview(self.character_counters_tree, c))
            self.character_counters_tree.column(col, width=width, anchor=anchor, stretch=True)
        self.character_counters_tree.pack(fill="both", expand=True, side="left")
        counters_ysb = ttk.Scrollbar(counters_frame, orient="vertical", command=self.character_counters_tree.yview)
        self.character_counters_tree.configure(yscrollcommand=counters_ysb.set)
        counters_ysb.pack(side="right", fill="y")

    def _refresh_character_view(self) -> None:
        """Read values from the save file into the Character tab widgets."""
        if not hasattr(self, "character_nickname_var"):
            return
        save_path = Path(self.save_var.get())
        # Clear tables up front so a stale view never lingers after a missing/bad save.
        for tree in (self.character_skills_tree, self.character_counters_tree):
            for row in tree.get_children(""):
                tree.delete(row)
        # Reloading discards any uncommitted per-skill edits.
        self._pending_skill_changes.clear()
        self._character_skill_row_ids.clear()
        self._character_skill_current.clear()

        if not save_path.exists():
            for var in (
                self.character_nickname_var,
                self.character_level_var,
                self.character_xp_var,
                self.character_next_goal_var,
                self.character_skill_points_var,
            ):
                var.set("")
            self.character_account_id_var.set("-")
            self.character_auth_chip_var.set("-")
            self._character_loaded = False
            if hasattr(self, "status_var"):
                self.status_var.set("Save file not found; character view is empty.")
            return

        try:
            data = load_save(save_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load save", f"{type(exc).__name__}: {exc}")
            self._character_loaded = False
            return

        acc = data.get("AccountDto") or {}
        exp = acc.get("ExperienceDto") or {}
        skills_dto = acc.get("SkillsDto") or {}

        self.character_nickname_var.set(str(acc.get("Nickname", "")))
        self.character_account_id_var.set(str(acc.get("AccountId", "-")) or "-")
        self.character_auth_chip_var.set(str(acc.get("AuthChipTemplateId", "-")) or "-")
        self.character_level_var.set(str(exp.get("Level", "")))
        self.character_xp_var.set(str(exp.get("ExperiencePoints", "")))
        self.character_next_goal_var.set(str(exp.get("NextLevelExperienceGoal", "")))
        self.character_skill_points_var.set(str(skills_dto.get("SkillPointsCount", "")))

        for entry in skills_dto.get("Skills", []) or []:
            try:
                sid = int(entry.get("Id"))
            except (TypeError, ValueError):
                continue
            level_val = entry.get("Level", "")
            goal_val = entry.get("NextLevelExperienceGoal", "")
            row_id = self.character_skills_tree.insert(
                "",
                "end",
                values=(sid, level_val, goal_val),
            )
            self._character_skill_row_ids[row_id] = sid
            self._character_skill_current[sid] = {
                "Level": int(level_val) if isinstance(level_val, (int, float)) else (int(level_val) if str(level_val).strip().isdigit() else 0),
                "NextLevelExperienceGoal": int(goal_val) if isinstance(goal_val, (int, float)) else (int(goal_val) if str(goal_val).strip().lstrip("-").isdigit() else 0),
            }

        for group in acc.get("Counters", {}).get("Counters", []) or []:
            group_id = group.get("$t", "")
            last_set = group.get("LastSetAtUtc", "")
            stats = group.get("All", {}) or {}
            if not stats:
                self.character_counters_tree.insert("", "end", values=(group_id, "(no stats)", "", last_set))
                continue
            for stat_name, value in stats.items():
                self.character_counters_tree.insert(
                    "",
                    "end",
                    values=(group_id, stat_name, value, last_set),
                )

        self._character_loaded = True
        if hasattr(self, "status_var"):
            self.status_var.set(
                f"Character: Level {exp.get('Level', '?')}, "
                f"{exp.get('ExperiencePoints', '?')} XP, "
                f"{skills_dto.get('SkillPointsCount', '?')} unspent skill points."
            )

    def _on_character_skill_double_click(self, _event: object = None) -> None:
        self._edit_selected_skill()

    def _edit_selected_skill(self) -> None:
        """Open an inline editor for the currently-selected skill row."""
        sel = self.character_skills_tree.selection()
        if not sel:
            return
        row_id = sel[0]
        sid = self._character_skill_row_ids.get(row_id)
        if sid is None:
            return
        current = self._character_skill_current.get(sid, {"Level": 0, "NextLevelExperienceGoal": 0})
        pending = self._pending_skill_changes.get(sid, {})
        # Pre-fill with pending edit if there is one, otherwise the save value.
        initial_level = pending.get("Level", current.get("Level", 0))
        initial_goal = pending.get("NextLevelExperienceGoal", current.get("NextLevelExperienceGoal", 0))

        dialog = tk.Toplevel(self)
        dialog.title(f"Edit skill {sid}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        frm = ttk.Frame(dialog, padding=(12, 10, 12, 10))
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Skill Id: {sid}", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=f"Save value -> Level: {current.get('Level', '?')}, "
                            f"Next-level goal: {current.get('NextLevelExperienceGoal', '?')}",
                  foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))

        level_var = tk.StringVar(value=str(initial_level))
        goal_var = tk.StringVar(value=str(initial_goal))
        ttk.Label(frm, text="Level:").grid(row=2, column=0, sticky="w")
        level_entry = ttk.Entry(frm, textvariable=level_var, width=14)
        level_entry.grid(row=2, column=1, sticky="w", padx=(6, 0))
        ttk.Label(frm, text="Next-level goal:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        goal_entry = ttk.Entry(frm, textvariable=goal_var, width=14)
        goal_entry.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(6, 0))

        error_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=error_var, foreground="#b91c1c").grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))

        def parse_field(value: str, label: str) -> Optional[int]:
            text = (value or "").strip()
            if text == "":
                return None
            return int(text)  # may raise ValueError; caller handles

        def on_apply() -> None:
            try:
                new_level = parse_field(level_var.get(), "Level")
                new_goal = parse_field(goal_var.get(), "Next-level goal")
            except ValueError:
                error_var.set("Both values must be integers (or blank to leave unchanged).")
                return
            staged: dict[str, Optional[int]] = {}
            if new_level is not None and new_level != current.get("Level"):
                staged["Level"] = new_level
            if new_goal is not None and new_goal != current.get("NextLevelExperienceGoal"):
                staged["NextLevelExperienceGoal"] = new_goal
            if staged:
                self._pending_skill_changes[sid] = staged
            else:
                # User cleared edits or matched the save -> drop any pending entry.
                self._pending_skill_changes.pop(sid, None)
            self._render_skill_row(row_id, sid)
            dialog.destroy()

        def on_revert() -> None:
            self._pending_skill_changes.pop(sid, None)
            self._render_skill_row(row_id, sid)
            dialog.destroy()

        ttk.Button(btn_row, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(btn_row, text="Revert pending", command=on_revert).pack(side="right", padx=(0, 6))
        ttk.Button(btn_row, text="Stage edit", command=on_apply).pack(side="right", padx=(0, 6))

        dialog.bind("<Return>", lambda _e: on_apply())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        level_entry.focus_set()
        level_entry.select_range(0, "end")

        # Center over the main window.
        self.update_idletasks()
        dx = self.winfo_rootx() + (self.winfo_width() // 2) - 130
        dy = self.winfo_rooty() + (self.winfo_height() // 2) - 80
        dialog.geometry(f"+{max(0, dx)}+{max(0, dy)}")

    def _render_skill_row(self, row_id: str, sid: int) -> None:
        """Repaint a skills_tree row using current save + any pending edits."""
        cur = self._character_skill_current.get(sid, {"Level": 0, "NextLevelExperienceGoal": 0})
        pending = self._pending_skill_changes.get(sid)
        if pending:
            level_display = pending.get("Level", cur.get("Level", ""))
            goal_display = pending.get("NextLevelExperienceGoal", cur.get("NextLevelExperienceGoal", ""))
            tags = ("pending",)
        else:
            level_display = cur.get("Level", "")
            goal_display = cur.get("NextLevelExperienceGoal", "")
            tags = ()
        self.character_skills_tree.item(row_id, values=(sid, level_display, goal_display), tags=tags)

    def _apply_character_changes(self) -> None:
        """Validate the editable fields and write them back to the save file."""
        if not getattr(self, "_character_loaded", False):
            messagebox.showwarning(
                "Nothing to apply",
                "Character data has not been loaded yet. Click 'Reload from save' first.",
            )
            return
        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return

        def parse_optional_int(value: str, label: str) -> Optional[int]:
            text = (value or "").strip()
            if text == "":
                return None
            try:
                return int(text)
            except ValueError:
                raise ValueError(f"{label} must be an integer (got {text!r}).")

        try:
            level = parse_optional_int(self.character_level_var.get(), "Level")
            xp = parse_optional_int(self.character_xp_var.get(), "XP")
            next_goal = parse_optional_int(self.character_next_goal_var.get(), "Next-level goal")
            skill_points = parse_optional_int(self.character_skill_points_var.get(), "Unspent skill points")
        except ValueError as exc:
            messagebox.showerror("Invalid value", str(exc))
            return

        nickname_new = (self.character_nickname_var.get() or "").strip()

        try:
            data = load_save(save_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load save", f"{type(exc).__name__}: {exc}")
            return

        acc = data.get("AccountDto") or {}
        exp = acc.get("ExperienceDto") or {}
        skills_dto = acc.get("SkillsDto") or {}
        changes: list[str] = []

        if nickname_new != str(acc.get("Nickname", "")):
            changes.append(f"Nickname: {acc.get('Nickname', '')!r} -> {nickname_new!r}")
        if level is not None and level != exp.get("Level"):
            changes.append(f"Level: {exp.get('Level')} -> {level}")
        if xp is not None and xp != exp.get("ExperiencePoints"):
            changes.append(f"XP: {exp.get('ExperiencePoints')} -> {xp}")
        if next_goal is not None and next_goal != exp.get("NextLevelExperienceGoal"):
            changes.append(f"Next-level goal: {exp.get('NextLevelExperienceGoal')} -> {next_goal}")
        if skill_points is not None and skill_points != skills_dto.get("SkillPointsCount"):
            changes.append(f"Skill points: {skills_dto.get('SkillPointsCount')} -> {skill_points}")

        # Per-skill pending edits collected via the inline editor.
        skill_changes_filtered: dict[int, dict[str, Optional[int]]] = {}
        current_by_id: dict[int, dict[str, int]] = {}
        for entry in skills_dto.get("Skills", []) or []:
            try:
                sid = int(entry.get("Id"))
            except (TypeError, ValueError):
                continue
            current_by_id[sid] = {
                "Level": int(entry.get("Level", 0) or 0),
                "NextLevelExperienceGoal": int(entry.get("NextLevelExperienceGoal", 0) or 0),
            }
        for sid, upd in self._pending_skill_changes.items():
            cur = current_by_id.get(sid)
            if cur is None:
                changes.append(f"Skill {sid}: not present in save (skipped)")
                continue
            this_skill: dict[str, Optional[int]] = {}
            new_level = upd.get("Level")
            new_goal = upd.get("NextLevelExperienceGoal")
            if new_level is not None and new_level != cur["Level"]:
                changes.append(f"Skill {sid} Level: {cur['Level']} -> {new_level}")
                this_skill["Level"] = new_level
            if new_goal is not None and new_goal != cur["NextLevelExperienceGoal"]:
                changes.append(f"Skill {sid} Next-level goal: {cur['NextLevelExperienceGoal']} -> {new_goal}")
                this_skill["NextLevelExperienceGoal"] = new_goal
            if this_skill:
                skill_changes_filtered[sid] = this_skill

        if not changes:
            messagebox.showinfo("No changes", "Character fields match the save; nothing to write.")
            return

        if not messagebox.askyesno(
            "Apply character changes?",
            "About to write the following changes to:\n  "
            f"{save_path}\n\n" + "\n".join(changes) + "\n\nA timestamped backup will be created.",
        ):
            return

        try:
            if nickname_new != str(acc.get("Nickname", "")):
                set_nickname(data, nickname_new)
            if level is not None or xp is not None or next_goal is not None:
                set_experience(data, level=level, xp=xp, next_goal=next_goal)
            if skill_points is not None and skill_points != skills_dto.get("SkillPointsCount"):
                set_skill_points(data, skill_points)
            if skill_changes_filtered:
                skill_summary = set_skill_levels(data, skill_changes_filtered)
                if skill_summary["missing"]:
                    self.log.insert(
                        "end",
                        f"  WARNING: skill ids not in save, skipped: {skill_summary['missing']}\n",
                    )
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not write save", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        self.log.insert("end", "Character changes applied:\n")
        for change in changes:
            self.log.insert("end", f"  {change}\n")
        self.log.see("end")
        self.status_var.set(f"Applied {len(changes)} character change(s).")
        self._refresh_character_view()

    # ---------- persisted settings ----------
    def _load_settings(self) -> None:
        """Apply persisted settings from SETTINGS_PATH (if present)."""
        try:
            if not SETTINGS_PATH.exists():
                return
            with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return

        def _set_str(var_name: str, key: str) -> None:
            val = data.get(key)
            if isinstance(val, str) and val and hasattr(self, var_name):
                getattr(self, var_name).set(val)

        def _set_bool(var_name: str, key: str) -> None:
            val = data.get(key)
            if isinstance(val, bool) and hasattr(self, var_name):
                getattr(self, var_name).set(val)

        def _set_int(var_name: str, key: str) -> None:
            val = data.get(key)
            if isinstance(val, int) and not isinstance(val, bool) and hasattr(self, var_name):
                getattr(self, var_name).set(val)

        _set_str("save_var", "save_path")
        _set_str("csv_var", "csv_path")
        _set_int("backup_keep_var", "backup_keep")
        _set_bool("group_add_categories_var", "group_add_categories")
        _set_bool("group_inventory_containers_var", "group_inventory_containers")
        _set_str("inventory_view_mode_var", "inventory_view_mode")
        _set_bool("inventory_weapons_first_var", "inventory_weapons_first")

        geom = data.get("window_geometry")
        if isinstance(geom, str) and re.match(r"^\d+x\d+([+-]\d+[+-]\d+)?$", geom):
            try:
                self.geometry(geom)
            except tk.TclError:
                pass

    def _save_settings(self) -> None:
        """Write current settings to SETTINGS_PATH. Silent on failure."""
        data: dict[str, object] = {}
        if hasattr(self, "save_var"):
            data["save_path"] = self.save_var.get()
        if hasattr(self, "csv_var"):
            data["csv_path"] = self.csv_var.get()
        if hasattr(self, "backup_keep_var"):
            try:
                data["backup_keep"] = int(self.backup_keep_var.get())
            except (tk.TclError, ValueError):
                pass
        if hasattr(self, "group_add_categories_var"):
            data["group_add_categories"] = bool(self.group_add_categories_var.get())
        if hasattr(self, "group_inventory_containers_var"):
            data["group_inventory_containers"] = bool(self.group_inventory_containers_var.get())
        if hasattr(self, "inventory_view_mode_var"):
            data["inventory_view_mode"] = self.inventory_view_mode_var.get()
        if hasattr(self, "inventory_weapons_first_var"):
            data["inventory_weapons_first"] = bool(self.inventory_weapons_first_var.get())
        try:
            data["window_geometry"] = self.geometry()
        except tk.TclError:
            pass
        try:
            with SETTINGS_PATH.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    # ---------- catalog ----------
    def _load_default_catalog(self) -> None:
        path = Path(self.csv_var.get())
        if not path.exists():
            messagebox.showerror("CSV missing", f"Could not find {path}")
            return
        try:
            self.catalog = _with_builtin_catalog_rows(_load_catalog(path))
            self.catalog_by_template = {
                (row.get("ItemID") or "").strip(): row
                for row in self.catalog
                if (row.get("ItemID") or "").strip()
            }
            self.catalog_by_name_compact = {
                self._compact_search_text(row.get("ItemName", "")): row
                for row in self.catalog
                if self._compact_search_text(row.get("ItemName", ""))
            }
            self.stack_max_by_template = {}
            for row in self.catalog:
                template_id = (row.get("ItemID") or "").strip()
                raw_stack = (row.get("StackCapacity") or "").strip()
                if not template_id or not raw_stack:
                    continue
                try:
                    stack_max = int(float(raw_stack))
                except ValueError:
                    continue
                if stack_max > 0:
                    self.stack_max_by_template[template_id] = stack_max
            self.visual_category_by_template = self._build_visual_category_map(self.catalog)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("CSV error", str(exc))
            return
        self.status_var.set(f"Loaded {len(self.catalog)} items from {path.name}")
        self._apply_filter()

    @staticmethod
    def _build_visual_category_map(rows: list[dict[str, str]]) -> dict[str, str]:
        candidates = [row for row in rows if _is_addable_catalog_row(row)]
        category_counts = Counter(_visual_category(row) or "Uncategorized" for row in candidates)
        category_by_template: dict[str, str] = {}
        for row in rows:
            template_id = (row.get("ItemID") or "").strip()
            if not template_id:
                continue
            category = _visual_category(row) or "Uncategorized"
            top_level = _visual_top_level(row)
            if (
                top_level.lower() == "loot"
                and category.lower() != "loot"
                and category_counts.get(category, 0) <= 1
            ):
                category = top_level
            category_by_template[template_id] = category
        return category_by_template

    def _row_visual_category(self, row: dict[str, str]) -> str:
        template_id = (row.get("ItemID") or "").strip()
        if template_id and template_id in self.visual_category_by_template:
            return self.visual_category_by_template[template_id]
        return _visual_category(row) or "Uncategorized"

    # ---------- icons ----------
    @staticmethod
    def _is_sprite_icon_path(path: Path) -> bool:
        try:
            path.resolve().relative_to(SPRITE_ICON_DIR.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _icon_priority(path: Path) -> int:
        parts = {part.lower() for part in path.parts}
        if "exported_icons" in parts and "sprite" in parts:
            return ICON_PRIORITY_SPRITE
        return ICON_PRIORITY_DEFAULT

    def _add_icon_candidate(self, path: Path, *labels: object) -> None:
        if not path.exists():
            return
        if not self._is_sprite_icon_path(path):
            return
        if path in self.icon_candidate_paths:
            return
        self.icon_candidate_paths.add(path)
        priority = self._icon_priority(path)
        text_parts = [path.stem, *[str(label or "") for label in labels]]
        search_text = " ".join(text_parts).lower()
        tokens: set[str] = set()
        for part in text_parts:
            tokens.update(_icon_tokens(part))
            key = _normalize_icon_text(part)
            if key and priority >= self.icon_exact_priority.get(key, -1):
                self.icon_exact[key] = path
                self.icon_exact_priority[key] = priority
        self.icon_candidates.append((path, tokens, _normalize_icon_text(search_text), _normalize_icon_text(path.stem), priority))

    def _load_icons(self) -> None:
        self.icon_exact.clear()
        self.icon_exact_priority.clear()
        self.icon_candidates.clear()
        self.icon_candidate_paths.clear()
        self.icon_match_cache.clear()
        self.icon_name_match_cache.clear()
        self.photo_cache.clear()
        self.selected_icon_photo = None

        icon_dirs = [DEFAULT_ICON_DIR, *FALLBACK_ICON_DIRS]
        existing_dirs = [path for path in icon_dirs if path.exists()]
        if not existing_dirs:
            self.icon_preview_label.configure(image="", text="No icons\nexported yet")
            return

        for icon_dir in existing_dirs:
            before = len(self.icon_candidates)
            manifest = icon_dir / "icon_manifest.csv"
            if manifest.exists():
                try:
                    with manifest.open("r", encoding="utf-8", newline="") as fh:
                        for row in csv.DictReader(fh):
                            raw_path = Path(row.get("output_path") or "")
                            if not raw_path.is_absolute():
                                raw_path = SCRIPT_DIR / raw_path
                            self._add_icon_candidate(raw_path, row.get("asset_name"), row.get("path_id"), row.get("source_bundle"))
                except Exception as exc:  # noqa: BLE001
                    self.log.insert("end", f"Could not read icon manifest {manifest}: {type(exc).__name__}: {exc}\n")
            if len(self.icon_candidates) == before:
                for path in sorted(icon_dir.rglob("*.png")):
                    self._add_icon_candidate(path)

        count = len({path for path, _tokens, _search, _stem_key, _priority in self.icon_candidates})
        self.icon_preview_label.configure(image="", text=f"Icons\nloaded: {count}")
        if hasattr(self, "log"):
            dirs = ", ".join(str(path) for path in existing_dirs)
            self.log.insert("end", f"Loaded {count} exported icon image(s) from {dirs}\n")

    def _exact_icon_path_for_name(self, name: object) -> Optional[Path]:
        key = _normalize_icon_text(name)
        if not key:
            return None
        return self.icon_exact.get(key)

    def _best_icon_path_for_name(self, name: object) -> Optional[Path]:
        key = _normalize_icon_text(name)
        if not key:
            return None
        if key in self.icon_name_match_cache:
            return self.icon_name_match_cache[key]
        exact = self.icon_exact.get(key)
        if exact is not None:
            self.icon_name_match_cache[key] = exact
            return exact

        name_tokens = _icon_tokens(name)
        best: tuple[int, Path] | None = None
        for path, tokens, search_text, stem_key, priority in self.icon_candidates:
            score = 0
            if stem_key == key:
                score = 10_000
            elif stem_key.endswith(key):
                score = 9_000 - len(stem_key)
            elif key in stem_key:
                score = 8_000 - len(stem_key)
            elif key in search_text:
                score = 7_000 - len(search_text)
            elif name_tokens:
                overlap = len(name_tokens & tokens)
                if overlap >= max(2, min(3, len(name_tokens))):
                    score = overlap * 100 - abs(len(tokens) - len(name_tokens))
            weighted_score = (score * 100) + priority if score else 0
            if weighted_score and (best is None or weighted_score > best[0]):
                best = (weighted_score, path)
        result = best[1] if best else None
        self.icon_name_match_cache[key] = result
        return result

    @staticmethod
    def _base_item_name_candidates(item_name: object) -> list[str]:
        text = str(item_name or "").strip()
        if not text:
            return []
        candidates: list[str] = []
        suffix_patterns = (
            r"[\s_]+(?:A|B|C|D)-(?:II|III)$",
            r"[\s_]+(?:A|B|C|D)$",
            r"[\s_]+(?:AP|EXP|HP|E)$",
            r"\s+\d+$",
        )
        for pattern in suffix_patterns:
            candidate = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            if candidate and candidate != text and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _catalog_icon_labels(row: dict[str, str]) -> list[str]:
        labels = [row.get("ItemName", "")]
        for key in ("VisualName", "CatalogVisualPath", "IconVisualName", "CatalogIconPath", "DroppedVisualName", "CatalogDroppedPath"):
            value = row.get(key) or ""
            if value:
                labels.append(Path(value).stem)
        return [label for label in labels if label]

    def _catalog_row_by_item_name(self, item_name: object) -> Optional[dict[str, str]]:
        key = self._compact_search_text(item_name)
        if not key:
            return None
        return self.catalog_by_name_compact.get(key)

    def _special_icon_aliases(self, item_name: object, meta: dict[str, str]) -> list[str]:
        name = str(item_name or meta.get("ItemName") or "").strip()
        aliases = list(SPECIAL_ICON_ALIASES.get(name, ()))
        aliases.extend(MARKET_CATEGORY_ICON_ALIASES.get(name, ()))
        if re.match(r"^Camo_\d+", name, flags=re.IGNORECASE):
            aliases.extend(CAMO_ICON_ALIASES)
        for label in self._catalog_icon_labels(meta):
            label_key = _normalize_icon_text(label)
            if label_key in {"ammo1270", "boxammo12reg"}:
                aliases.extend(["Ammo_12_Piercing", "BoxAmmo_12Reg"])
            elif label_key in {"ammo1270ap", "boxammo12ap"}:
                aliases.extend(["Ammo_12_PiercingAP", "BoxAmmo_12AP", "Ammo_12_Piercing"])
            elif label_key in {"boxammo12hp"}:
                aliases.extend(["Ammo_12_PiercingEXP", "BoxAmmo_12HP", "Ammo_12_Piercing"])
            elif label_key == "ammo1270nl":
                aliases.append("Ammo_12NL")
        out: list[str] = []
        for alias in aliases:
            if alias and alias not in out:
                out.append(alias)
        return out

    def _reload_icons_and_views(self) -> None:
        self._load_icons()
        self._apply_filter()
        self._refresh_inventory_view()

    def _icon_path_for(self, template_id: object, item_name: object) -> Optional[Path]:
        cache_key = (str(template_id or ""), str(item_name or ""))
        if cache_key in self.icon_match_cache:
            return self.icon_match_cache[cache_key]

        for value in (template_id, item_name):
            key = _normalize_icon_text(value)
            if key in self.icon_exact:
                self.icon_match_cache[cache_key] = self.icon_exact[key]
                return self.icon_exact[key]

        meta = self.catalog_by_template.get(str(template_id or ""), {})
        for alias in self._special_icon_aliases(item_name, meta):
            path = self._exact_icon_path_for_name(alias)
            if path is not None:
                self.icon_match_cache[cache_key] = path
                return path

        catalog_labels = self._catalog_icon_labels(meta)
        for label in [*catalog_labels, item_name]:
            path = self._exact_icon_path_for_name(label)
            if path is not None:
                self.icon_match_cache[cache_key] = path
                return path

        for base_name in self._base_item_name_candidates(item_name):
            path = self._exact_icon_path_for_name(base_name) or self._best_icon_path_for_name(base_name)
            if path is not None:
                self.icon_match_cache[cache_key] = path
                return path
            base_row = self._catalog_row_by_item_name(base_name)
            if base_row:
                for label in self._catalog_icon_labels(base_row):
                    path = self._exact_icon_path_for_name(label) or self._best_icon_path_for_name(label)
                    if path is not None:
                        self.icon_match_cache[cache_key] = path
                        return path

        category_icon = CATEGORY_ICON_BY_ID.get(str(meta.get("CategoryID") or ""))
        if category_icon:
            category_icon_path = self.icon_exact.get(_normalize_icon_text(category_icon))
            if category_icon_path is not None:
                self.icon_match_cache[cache_key] = category_icon_path
                return category_icon_path

        if meta:
            visual_category = self._row_visual_category(meta)
            for alias in VISUAL_CATEGORY_ICON_ALIASES.get(visual_category, ()):
                path = self._exact_icon_path_for_name(alias)
                if path is not None:
                    self.icon_match_cache[cache_key] = path
                    return path

        for label in catalog_labels:
            path = self._best_icon_path_for_name(label)
            if path is not None:
                self.icon_match_cache[cache_key] = path
                return path

        path = self._best_icon_path_for_name(item_name)
        if path is not None:
            self.icon_match_cache[cache_key] = path
            return path

        self.icon_match_cache[cache_key] = None
        return None

    def _category_icon_path_for(self, category: str, rows: Iterable[dict[str, str]]) -> Optional[Path]:
        for alias in VISUAL_CATEGORY_ICON_ALIASES.get(str(category or ""), ()):
            path = self._exact_icon_path_for_name(alias)
            if path is not None:
                return path

        path = self._exact_icon_path_for_name(category)
        if path is not None:
            return path

        category_ids = Counter(str(row.get("CategoryID") or "") for row in rows)
        for category_id, _count in category_ids.most_common():
            alias = CATEGORY_ICON_BY_ID.get(category_id)
            if not alias:
                continue
            path = self._exact_icon_path_for_name(alias)
            if path is not None:
                return path
        return None

    def _tree_category_icon(self, category: str, rows: Iterable[dict[str, str]]) -> Optional[tk.PhotoImage]:
        path = self._category_icon_path_for(category, rows)
        return self._get_icon_photo(path, 20) if path else None

    def _get_icon_photo(self, path: Path, max_size: int) -> Optional[tk.PhotoImage]:
        cache_key = (path, max_size)
        if cache_key in self.photo_cache:
            return self.photo_cache[cache_key]
        try:
            image = tk.PhotoImage(file=str(path))
            factor = max(1, math.ceil(max(image.width(), image.height()) / max_size))
            if factor > 1:
                image = image.subsample(factor, factor)
        except tk.TclError:
            return None
        self.photo_cache[cache_key] = image
        return image

    def _show_selected_icon(self, template_id: object, item_name: object) -> None:
        path = self._icon_path_for(template_id, item_name)
        photo = self._get_icon_photo(path, 96) if path else None
        self.selected_icon_photo = photo
        if photo is None:
            message = "No icon\nfound" if self.icon_candidates else "No icons\nexported yet"
            self.icon_preview_label.configure(image="", text=message)
            return
        self.icon_preview_label.configure(image=photo, text="")

    def _tree_icon(self, template_id: object, item_name: object) -> Optional[tk.PhotoImage]:
        path = self._icon_path_for(template_id, item_name)
        return self._get_icon_photo(path, 20) if path else None

    # ---------- sorting ----------
    @staticmethod
    def _sort_key(value: object) -> tuple[int, int, float, float, str]:
        text = "" if value is None else str(value).strip()
        if not text:
            return (3, 0, 0.0, 0.0, "")

        lowered = text.lower()
        if "x" in lowered:
            left, right, *_rest = [part.strip() for part in lowered.split("x", 2)]
            try:
                return (0, 1, float(left), float(right), "")
            except ValueError:
                pass

        if "," in text:
            left, right, *_rest = [part.strip() for part in text.split(",", 2)]
            try:
                return (0, 1, float(left), float(right), "")
            except ValueError:
                pass

        try:
            return (0, 0, float(text), 0.0, "")
        except ValueError:
            if "/" in text:
                parts = [part.strip() for part in text.split("/", 1)]
                try:
                    return (0, 1, float(parts[0]), float(parts[1]), "")
                except (IndexError, ValueError):
                    pass
            return (1, 0, 0.0, 0.0, lowered)

    def _sort_treeview(self, tree: ttk.Treeview, column: str) -> None:
        sort_id = (str(tree), column)
        reverse = not self._sort_reverse.get(sort_id, False)
        self._sort_reverse[sort_id] = reverse
        self._sort_state[str(tree)] = (column, reverse)

        self._apply_treeview_sort(tree, column, reverse)

    def _apply_treeview_sort(self, tree: ttk.Treeview, column: str, reverse: bool) -> None:

        def row_value(row_id: str) -> object:
            if column == "#0":
                return tree.item(row_id, "text")
            return tree.set(row_id, column)

        def sort_children(parent: str) -> None:
            rows = list(tree.get_children(parent))
            rows.sort(key=lambda row_id: self._sort_key(row_value(row_id)), reverse=reverse)
            for index, row_id in enumerate(rows):
                tree.move(row_id, parent, index)
                sort_children(row_id)

        sort_children("")

    def _restore_treeview_sort(self, tree: ttk.Treeview) -> None:
        sort_state = self._sort_state.get(str(tree))
        if sort_state is None:
            return
        column, reverse = sort_state
        self._apply_treeview_sort(tree, column, reverse)

    def _on_add_tree_open(self, _event: object = None) -> None:
        row_id = self.tree.focus()
        category = self.add_category_refs.get(row_id)
        if category is not None:
            self.add_category_open_state[category] = True

    def _on_add_tree_close(self, _event: object = None) -> None:
        row_id = self.tree.focus()
        category = self.add_category_refs.get(row_id)
        if category is not None:
            self.add_category_open_state[category] = False

    def _collapse_all_add_categories(self) -> None:
        for row_id, category in list(self.add_category_refs.items()):
            self.tree.item(row_id, open=False)
            self.add_category_open_state[category] = False
        self.status_var.set("Collapsed all Add Items categories.")

    def _expand_all_add_categories(self) -> None:
        for row_id, category in list(self.add_category_refs.items()):
            self.tree.item(row_id, open=True)
            self.add_category_open_state[category] = True
        self.status_var.set("Expanded all Add Items categories.")

    def _reload_catalog_and_views(self) -> None:
        self._load_default_catalog()
        self._refresh_containers()

    def _pull_new_csv_from_game(self) -> None:
        extractor = SCRIPT_DIR / "extract_item_catalog_from_game.py"
        if not extractor.exists():
            messagebox.showerror("Extractor missing", f"Could not find {extractor}")
            return

        initial_dir = DEFAULT_GAME_DIR if DEFAULT_GAME_DIR.exists() else SCRIPT_DIR
        game_dir_raw = filedialog.askdirectory(
            title="Select Cargo Hunters install folder",
            initialdir=str(initial_dir),
            mustexist=True,
        )
        if not game_dir_raw:
            return

        game_dir = Path(game_dir_raw)
        expected_data_dir = game_dir / "CargoHunters_Data"
        if not expected_data_dir.exists() and not messagebox.askyesno(
            "Folder does not look like Cargo Hunters",
            f"Could not find CargoHunters_Data under:\n\n{game_dir}\n\nContinue anyway?",
        ):
            return

        csv_path = Path(self.csv_var.get())
        if not messagebox.askyesno(
            "Pull new item CSV?",
            "This will extract item templates and shop inventory data from the selected game install, then:\n\n"
            f"• back up the current CSV if it exists\n"
            f"• replace: {csv_path}\n"
            "• reload the Add Items and inventory metadata\n\n"
            "If UnityPy is missing, the extractor will try to install it first. Continue?",
        ):
            return

        command = [
            sys.executable,
            str(extractor),
            "--game-dir",
            str(game_dir),
            "--replace",
            "--csv",
            str(csv_path),
            "--install-deps",
        ]
        self.status_var.set("Pulling fresh item CSV from game files…")
        self.update_idletasks()
        self.log.insert("end", f"\nPulling fresh item CSV from: {game_dir}\n")
        self.log.see("end")

        try:
            result = subprocess.run(
                command,
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.status_var.set("CSV pull timed out.")
            messagebox.showerror("CSV pull timed out", "The extractor did not finish within 5 minutes.")
            return
        except Exception as exc:  # noqa: BLE001
            self.status_var.set("CSV pull failed.")
            messagebox.showerror("CSV pull failed", f"{type(exc).__name__}: {exc}")
            return

        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if output:
            self.log.insert("end", output.rstrip() + "\n")
            self.log.see("end")

        if result.returncode != 0:
            tail = output[-2000:] if output else "No extractor output was captured."
            self.status_var.set("CSV pull failed.")
            messagebox.showerror("CSV pull failed", f"Extractor exited with code {result.returncode}.\n\n{tail}")
            return

        self._reload_catalog_and_views()
        self.status_var.set(f"Pulled fresh CSV from {game_dir.name} and reloaded {csv_path.name}.")
        messagebox.showinfo("CSV updated", f"Fresh item catalog pulled and loaded:\n\n{csv_path}")

    @staticmethod
    def _normalize_search_text(value: object) -> str:
        text = str(value or "")
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
        text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
        text = re.sub(r"[^A-Za-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text.lower()).strip()

    @staticmethod
    def _compact_search_text(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", EditorGUI._normalize_search_text(value))

    @staticmethod
    def _search_value_matches(value: object, query: str) -> bool:
        raw_value = str(value or "").lower()
        raw_query = str(query or "").strip().lower()
        if not raw_query:
            return True

        normalized_value = EditorGUI._normalize_search_text(value)
        normalized_query = EditorGUI._normalize_search_text(query)
        compact_value = EditorGUI._compact_search_text(value)
        compact_query = EditorGUI._compact_search_text(query)
        if raw_query in raw_value:
            return True
        if normalized_query and normalized_query in normalized_value:
            return True
        if compact_query and compact_query in compact_value:
            return True

        raw_terms = [term for term in re.split(r"\s+", raw_query) if term]
        normalized_terms = [term for term in normalized_query.split() if term]
        compact_terms = [EditorGUI._compact_search_text(term) for term in normalized_terms]
        return (
            bool(raw_terms) and all(term in raw_value or term in normalized_value for term in raw_terms)
        ) or (
            bool(normalized_terms) and all(term in normalized_value for term in normalized_terms)
        ) or (
            bool(compact_terms) and all(term in compact_value for term in compact_terms)
        )

    @staticmethod
    def _field_values(text: str, field: str) -> list[str]:
        haystack = str(text or "").lower()
        values = re.findall(rf"(?:^|\n){re.escape(field)}=([^\n]*)", haystack)
        if values:
            return values
        # Backward-compatible fallback for any older space-separated search blobs.
        return re.findall(rf"(?:^|\s){re.escape(field)}=([^\s]+)", haystack)

    @staticmethod
    def _matches_terms(text: str, query: str) -> bool:
        field_query = EditorGUI._field_query(query)
        if field_query is not None:
            field, wanted = field_query
            values = EditorGUI._field_values(text, field)
            if values:
                return any(EditorGUI._search_value_matches(value, wanted) for value in values)
            return EditorGUI._search_value_matches(text, wanted)
        return EditorGUI._search_value_matches(text, query)

    @staticmethod
    def _field_query(query: str) -> Optional[tuple[str, str]]:
        cleaned = re.sub(r"\s+", " ", query.strip().lower())
        cleaned = cleaned.replace("template id", "templateid")
        cleaned = cleaned.replace("item id", "itemid")
        cleaned = cleaned.replace("category id", "categoryid")
        cleaned = cleaned.replace("subcategory id", "subcategoryid")
        match = re.match(r"^(visualcategory|visualcat|vcat|categoryid|category|cat|subcategoryid|subcategory|subcat|name|source)\s*[:= ]\s*(.+)$", cleaned)
        if not match:
            return None
        field = match.group(1)
        value = match.group(2).strip()
        aliases = {
            "category": "visualcategory",
            "cat": "categoryid",
            "subcategory": "subcategoryid",
            "subcat": "subcategoryid",
            "visualcat": "visualcategory",
            "vcat": "visualcategory",
        }
        return aliases.get(field, field), value

    @staticmethod
    def _is_guid_search_value(value: object) -> bool:
        return bool(GUID_RE.search(str(value or "")))

    @staticmethod
    def _is_guid_search_field(field_name: object) -> bool:
        normalized = EditorGUI._normalize_search_text(field_name)
        compact = EditorGUI._compact_search_text(field_name)
        return normalized in GUID_SEARCH_FIELD_NAMES or compact in GUID_SEARCH_FIELD_NAMES

    @staticmethod
    def _add_search_field(parts: list[str], field_name: object, value: object, *aliases: object) -> None:
        if EditorGUI._is_guid_search_field(field_name):
            return
        if EditorGUI._is_guid_search_value(value):
            return
        parts.extend(str(part or "") for part in (field_name, *aliases, value) if str(part or ""))

    def _catalog_row_text(self, row: dict[str, str]) -> str:
        aliases = {
            "CategoryID": "CategoryID Category Id Cat",
            "SubcategoryID": "SubcategoryID Subcategory Id Subcat",
        }
        visual_category = self._row_visual_category(row)
        parts: list[str] = []
        for key, value in row.items():
            self._add_search_field(parts, key, value, aliases.get(key, ""))
        parts.extend([
            f"name={row.get('ItemName', '')}",
            f"name={_catalog_display_name(row)}",
            f"displayname={_catalog_display_name(row)}",
            f"category={visual_category}",
            f"visualcategory={visual_category}",
            f"vcat={visual_category}",
            f"categoryid={row.get('CategoryID', '')}",
            f"subcategoryid={row.get('SubcategoryID', '')}",
        ])
        parts.extend(_catalog_search_aliases(row))
        return "\n".join(parts)

    def _catalog_filter_sort_key(self, row: dict[str, str], query: str) -> tuple[int, str]:
        display_name = _catalog_display_name(row)
        if not query:
            return (0, display_name.lower())

        compact_query = self._compact_search_text(query)
        alias_blob = " ".join([display_name, *_catalog_search_aliases(row)])
        if compact_query and compact_query in self._compact_search_text(alias_blob):
            return (0, display_name.lower())
        if compact_query and compact_query in self._compact_search_text(row.get("ItemName", "")):
            return (1, display_name.lower())
        return (2, display_name.lower())

    def _apply_filter(self) -> None:
        q = self.search_var.get().strip().lower()
        addable_catalog = [row for row in self.catalog if _is_addable_catalog_row(row)]
        if q:
            self.filtered = [
                row for row in addable_catalog
                if self._matches_terms(self._catalog_row_text(row), q)
            ]
        else:
            self.filtered = list(addable_catalog)
        self.filtered.sort(key=lambda r: self._catalog_filter_sort_key(r, q))

        self.tree.delete(*self.tree.get_children())
        self.add_item_refs.clear()
        self.add_category_refs.clear()

        # Limit for performance; CSV has thousands of rows.
        visible_rows = self.filtered[:1000]
        group_by_category = bool(self.group_add_categories_var.get())
        self.tree.heading("#0", text="Category" if group_by_category else "Icon")
        self.tree.column("#0", width=170 if group_by_category else 48, minwidth=40 if not group_by_category else 130, stretch=False)

        def insert_item(parent: str, row: dict[str, str], category: str) -> None:
            size = f"{row.get('Width','')}x{row.get('Height','')}"
            values = (
                _catalog_display_name(row),
                row["ItemID"],
                category,
                row.get("CategoryID", ""),
                row.get("SubcategoryID", ""),
                row.get("BasePrice", ""),
                row.get("Weight", ""),
                size,
            )
            tag = self._color_tag(row.get("CategoryID", ""))
            item_options: dict = {"text": "", "values": values, "tags": (tag,) if tag else ()}
            icon = self._tree_icon(row["ItemID"], row["ItemName"])
            if icon is not None:
                item_options["image"] = icon
            item_id = self.tree.insert(parent, "end", **item_options)
            self.add_item_refs[item_id] = row

        grouped: dict[str, list[dict[str, str]]] = {}
        for row in visible_rows:
            category = self._row_visual_category(row)
            grouped.setdefault(category, []).append(row)

        if group_by_category:
            for category in sorted(grouped, key=str.lower):
                rows = sorted(grouped[category], key=lambda r: _catalog_display_name(r).lower())
                group_options = {
                    "text": f"{category} ({len(rows)})",
                    "values": ("", "", category, "", "", "", "", ""),
                    "open": self.add_category_open_state.get(category, True),
                }
                category_icon = self._tree_category_icon(category, rows)
                if category_icon is not None:
                    group_options["image"] = category_icon
                group_id = self.tree.insert("", "end", **group_options)
                self.add_category_refs[group_id] = category
                for row in rows:
                    insert_item(group_id, row, category)
        else:
            for row in sorted(visible_rows, key=lambda r: _catalog_display_name(r).lower()):
                insert_item("", row, self._row_visual_category(row))
        shown = min(len(self.filtered), 1000)
        self._restore_treeview_sort(self.tree)
        if group_by_category:
            self.status_var.set(f"Showing {shown} of {len(self.filtered)} matches across {len(grouped)} categories.")
        else:
            self.status_var.set(f"Showing {shown} of {len(self.filtered)} matches in a flat sortable list.")

    def _selected_add_rows(self) -> list[dict[str, str]]:
        return [self.add_item_refs[row_id] for row_id in self.tree.selection() if row_id in self.add_item_refs]

    def _add_tree_hover_text(self, row_id: str) -> str:
        row = self.add_item_refs.get(row_id)
        if row is None:
            category = self.add_category_refs.get(row_id)
            return f"Category: {category}\nExpand/collapse this group to show or hide its items." if category else ""
        return self._catalog_item_specs_text(row)

    def _catalog_item_specs_text(self, row: dict[str, str]) -> str:
        stack_capacity = (row.get("StackCapacity") or "").strip()
        if not stack_capacity:
            stack_capacity = str(self.stack_max_by_template.get(str(row.get("ItemID") or "")) or "")
        size = f"{row.get('Width','')}x{row.get('Height','')}".strip("x")
        return "\n".join([
            f"ItemName: {_catalog_display_name(row)}",
            f"Base Price: {row.get('BasePrice', '') or '(blank)'}",
            f"Weight: {row.get('Weight', '') or '(blank)'}",
            f"Inventory Size: {size or '(blank)'}",
            f"Stack Capacity: {stack_capacity or '(blank)'}",
            f"Category: {self._row_visual_category(row)}",
        ])

    def _on_select(self, _event: object = None) -> None:
        rows = self._selected_add_rows()
        if not rows:
            sel = self.tree.selection()
            if sel and sel[0] in self.add_category_refs:
                self.sel_name_var.set("(category)")
                self.sel_id_var.set("")
                self.icon_preview_label.configure(image="", text="Select an\nitem")
            return
        if len(rows) > 1:
            self.sel_name_var.set(f"{len(rows)} items selected")
            self.sel_id_var.set("Ctrl/Shift-click to adjust selection")
            self.icon_preview_label.configure(image="", text="Multiple\nitems")
            if self._auto_qty_template is not None:
                self.qty_var.set("")
                self._auto_qty_template = None
            return
        row = rows[0]
        name = row.get("ItemName", "")
        template_id = row.get("ItemID", "")
        self.sel_name_var.set(_catalog_display_name(row))
        self.sel_id_var.set(template_id)
        self._show_selected_icon(template_id, name)
        stack_max = self.stack_max_by_template.get(str(template_id)) or get_stack_count_max_for_template(str(template_id))
        if stack_max is not None and (not self.qty_var.get().strip() or self._auto_qty_template is not None):
            self.qty_var.set(str(stack_max))
            self._auto_qty_template = str(template_id)
        elif stack_max is None and self._auto_qty_template is not None:
            self.qty_var.set("")
            self._auto_qty_template = None

    # ---------- actions ----------
    def _pick_save(self) -> None:
        initial = Path(self.save_var.get())
        initial_dir = initial.parent if initial.exists() else DEFAULT_SAVE_DIR
        if not initial_dir.exists():
            initial_dir = SCRIPT_DIR
        path = filedialog.askopenfilename(
            title="Pick offline.save",
            initialdir=str(initial_dir),
            filetypes=[("Save files", "*.save *.json"), ("All files", "*.*")],
        )
        if path:
            self.save_var.set(path)
            self._refresh_containers()
            self._refresh_inventory_view()

    def _current_backup_keep(self) -> Optional[int]:
        """Read the spinbox; clamp to [1, 500]. None if widget not built yet."""
        if not hasattr(self, "backup_keep_var"):
            return None
        try:
            value = int(self.backup_keep_var.get())
        except (TypeError, ValueError, tk.TclError):
            return None
        if value < 1:
            return 1
        if value > 500:
            return 500
        return value

    def _prune_backups_now(self) -> None:
        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return
        keep = self._current_backup_keep()
        if keep is None:
            messagebox.showerror("Bad value", "Keep-last backups must be a positive integer.")
            return
        backups = list_save_backups(save_path)
        if len(backups) <= keep:
            messagebox.showinfo(
                "Nothing to prune",
                f"Found {len(backups)} backup file(s); keep-last is {keep}. Nothing to delete.",
            )
            return
        if not messagebox.askyesno(
            "Prune old backups?",
            f"Delete the oldest {len(backups) - keep} backup file(s) for\n  {save_path.name}\n"
            f"keeping the newest {keep}?",
        ):
            return
        deleted = prune_old_backups(save_path, keep=keep)
        self.log.insert("end", f"Pruned {len(deleted)} old backup(s).\n")
        for old in deleted:
            self.log.insert("end", f"  removed {old.name}\n")
        self.log.see("end")
        self.status_var.set(f"Pruned {len(deleted)} old backup(s); {keep} most recent kept.")

    def _refresh_containers(self) -> None:
        save_path = Path(self.save_var.get())
        if not save_path.exists():
            self.containers = []
            self.dest_combo["values"] = []
            self.dest_var.set("")
            self._set_inventory_container_values([])
            return
        try:
            data = load_save(save_path)
            names = load_template_names(Path(self.csv_var.get()))
            self.containers = discover_containers(data, names)
            # Surface empty container-class items (e.g. an equipped vest with
            # nothing in it) so they appear in the Container dropdown and the
            # Current Inventory view.
            self.containers.extend(self._synthesize_empty_containers(data, names, self.containers))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not scan containers", str(exc))
            self.containers = []
            self.dest_combo["values"] = []
            self.dest_var.set("")
            self._set_inventory_container_values([])
            return

        labels = [self._dest_label(c) for c in self.containers]
        inventory_labels = [self._dest_label(c) for c in self.containers if c.source in CURRENT_INVENTORY_SOURCES]
        self.dest_combo["values"] = labels
        if labels:
            self.dest_var.set(labels[0])
        self._set_inventory_container_values(inventory_labels)
        self._refresh_inventory_view()
        self.status_var.set(f"Found {len(self.containers)} containers.")

    @staticmethod
    def _dest_label(c: Container) -> str:
        dims = f"  [{c.grid_width}x{c.grid_height}]" if c.grid_width else ""
        return f"{c.label}{dims}"

    def _selected_container(self) -> Optional[Container]:
        label = self.dest_var.get()
        for c in self.containers:
            if self._dest_label(c) == label:
                return c
        return None

    def _set_inventory_container_values(self, labels: list[str]) -> None:
        if not hasattr(self, "inv_container_combo"):
            return
        values = ["All containers", *labels]
        self.inv_container_combo["values"] = values
        if self.inv_container_var.get() not in values:
            self.inv_container_var.set("All containers")

    def _inventory_containers_to_show(self) -> list[Container]:
        selected = self.inv_container_var.get()
        if selected and selected != "All containers":
            return [c for c in self.containers if c.source in CURRENT_INVENTORY_SOURCES and self._dest_label(c) == selected]
        return [c for c in self.containers if c.source in CURRENT_INVENTORY_SOURCES]

    def _is_container_template(self, template_id: str) -> bool:
        if not template_id:
            return False
        category = str(self._catalog_meta(template_id).get("CategoryID") or "")
        return category in CONTAINER_CATEGORY_IDS

    def _item_role(self, template_id: str) -> str:
        """Classify a template by gameplay role.

        Returns one of: ``"weapon"`` (whole gun, cat 1), ``"weapon_part"``
        (receiver/barrel/mag/sight/etc., cat 20), ``"container"`` (backpacks,
        vests, cases, key holders, etc.), or ``"item"`` (everything else).
        """
        if not template_id:
            return "item"
        category = str(self._catalog_meta(template_id).get("CategoryID") or "")
        if category == WEAPON_CATEGORY_ID:
            return "weapon"
        if category == WEAPON_PART_CATEGORY_ID:
            return "weapon_part"
        if category in CONTAINER_CATEGORY_IDS:
            return "container"
        return "item"

    def _classify_inventory_row(self, template_id: str, has_children: bool) -> str:
        """Return the Source column label for a row.

        Containers (incl. an empty vest) are labelled ``Equipment``. Weapons
        are labelled ``Items`` even though they physically have child parts,
        because the user perceives a gun as one thing, not a container.
        """
        role = self._item_role(template_id)
        if role == "container":
            return INVENTORY_SOURCE_EQUIPMENT_LABEL
        if role in ("weapon", "weapon_part", "item"):
            return INVENTORY_SOURCE_ITEMS_LABEL
        # Fallback: a non-container template that nonetheless has children
        # (rare; e.g. a quest item) is still treated as Equipment so the
        # parent expand surfaces its contents.
        if has_children:
            return INVENTORY_SOURCE_EQUIPMENT_LABEL
        return INVENTORY_SOURCE_ITEMS_LABEL

    @staticmethod
    def _grid_dims_from_item(item: dict) -> tuple[Optional[int], Optional[int]]:
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        def _as_int(value: object) -> Optional[int]:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                return None
            return ivalue if ivalue > 0 else None
        return _as_int(ad.get("BaseComponent_width")), _as_int(ad.get("BaseComponent_height"))

    def _synthesize_empty_containers(
        self,
        data: dict,
        names: dict[str, str],
        discovered: list[Container],
    ) -> list[Container]:
        """Return additional Container entries for items in inventory/equipment that
        are container-category templates but currently hold no children, so they
        still show up in the Current Inventory view and the Container dropdown.
        """
        existing_owner_ids = {(c.source, c.owner_item_id) for c in discovered}
        synthetic: list[Container] = []
        for source in CURRENT_INVENTORY_SOURCES:
            try:
                items = get_items_list(data, source)
            except KeyError:
                continue
            child_count: dict[str, int] = {}
            for it in items:
                pid = it.get("ParentId")
                if pid:
                    child_count[pid] = child_count.get(pid, 0) + 1
            for it in items:
                iid = it.get("Id")
                if not iid:
                    continue
                if child_count.get(iid, 0) > 0:
                    continue  # already added by discover_containers
                if (source, iid) in existing_owner_ids:
                    continue
                tid = it.get("TemplateId") or ""
                if not self._is_container_template(tid):
                    continue
                gw, gh = self._grid_dims_from_item(it)
                label_base = names.get(tid) or tid[:8] or "container"
                prefix = "Inv" if source == "inventory" else "Equip"
                synthetic.append(Container(
                    label=f"{prefix}: {label_base}",
                    source=source,
                    owner_item_id=iid,
                    grid_width=gw,
                    grid_height=gh,
                    template_id=tid,
                ))
        return synthetic

    @staticmethod
    def _item_extra_value(item: dict, *keys: str) -> str:
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        for key in keys:
            if key in ad:
                return str(ad[key])
        return ""

    @staticmethod
    def _observed_stack_max(data: dict) -> dict[str, int]:
        observed: dict[str, int] = {}
        for source in CURRENT_INVENTORY_SOURCES:
            try:
                items = get_items_list(data, source)
            except KeyError:
                continue
            for item in items:
                ad = ((item.get("AdditionalData") or {}).get("_data") or {})
                if "StackableComponent_quantity" not in ad:
                    continue
                try:
                    quantity = int(ad["StackableComponent_quantity"])
                except (TypeError, ValueError):
                    continue
                template_id = (item.get("TemplateId") or "").strip()
                if template_id:
                    observed[template_id] = max(quantity, observed.get(template_id, 0))
        return observed

    def _item_stack_value(self, item: dict, observed_stack_max: Optional[dict[str, int]] = None) -> str:
        raw = self._item_extra_value(item, "StackableComponent_quantity")
        stack_max = self.stack_max_by_template.get((item.get("TemplateId") or "").strip()) or get_stack_count_max(item, observed_stack_max)
        if not raw:
            if stack_max is None:
                return ""
            return f"1 / {stack_max}"
        if stack_max is None:
            return raw
        try:
            current = int(raw)
        except ValueError:
            return raw
        if current <= stack_max:
            return f"{current} / {stack_max}"
        return str(current)

    def _catalog_meta(self, template_id: str) -> dict[str, str]:
        return self.catalog_by_template.get(template_id, {})

    @staticmethod
    def _color_tag(category_id: str) -> str:
        """Return the treeview tag name for colour coding, or '' if none."""
        return CATEGORY_COLOR_TAG.get((category_id or "").strip(), "")

    @staticmethod
    def _category_name(category_id: str) -> str:
        """Return a short readable name for a numeric Cargo Hunters CategoryID.

        Falls back to ``"Cat {id}"`` for unknown ids, or ``""`` if the id is empty.
        """
        cid = (category_id or "").strip()
        if not cid:
            return ""
        return CATEGORY_NAME_BY_ID.get(cid, f"Cat {cid}")

    def _inventory_tree_hover_text(self, row_id: str) -> str:
        """Tooltip text for a row in the Current Inventory tree.

        Shows the same catalog spec block as the Add Items tooltip plus the
        per-instance state of the row (qty in stack, position, condition,
        durability). Container header rows show a brief summary.
        """
        if not row_id:
            return ""
        # Try leaf/weapon-expand row first (these set inventory_item_refs).
        item_ref = self.inventory_item_refs.get(row_id)
        if item_ref is not None:
            source, item_id, name = item_ref
            item = self._find_inventory_item(source, item_id)
            if item is None:
                return f"ItemName: {name}\nItem Id: {item_id}\nSource: {source}\n(item not found in current save)"
            template_id = (item.get("TemplateId") or "").strip()
            meta = self._catalog_meta(template_id)
            display_name = _catalog_display_name(meta) if meta else name
            type_label = self._row_visual_category(meta) if meta else ""
            cat_id = meta.get("CategoryID", "") if meta else ""
            cat_name = self._category_name(cat_id)
            sub_id = meta.get("SubcategoryID", "") if meta else ""
            w = meta.get("Width", "") if meta else ""
            h = meta.get("Height", "") if meta else ""
            size = f"{w}x{h}".strip("x") if (w or h) else ""
            base_price = (meta.get("BasePrice", "") if meta else "") or "(blank)"
            weight = (meta.get("Weight", "") if meta else "") or "(blank)"
            stack_capacity = (meta.get("StackCapacity", "").strip() if meta else "")
            if not stack_capacity:
                stack_capacity = str(self.stack_max_by_template.get(template_id) or "")
            stack_capacity = stack_capacity or "(blank)"
            qty = self._item_stack_value(item, self.stack_max_by_template)
            position = self._item_position(item) or "(equipped/none)"
            condition = self._item_condition_value(item) or "(n/a)"
            durability = self._item_durability_value(item) or "(n/a)"
            source_label = self._classify_inventory_row(template_id, False)
            lines = [
                f"ItemName: {display_name}",
                f"Type: {type_label or 'Uncategorized'}",
                f"Category: {cat_name or '(blank)'} (id {cat_id or '?'})  Sub: {sub_id or '(blank)'}",
                f"Inventory Size: {size or '(blank)'}",
                f"Base Price: {base_price}",
                f"Weight: {weight}",
                f"Stack Capacity: {stack_capacity}",
                f"Qty: {qty or '(blank)'}",
                f"Position: {position}",
                f"Condition: {condition}",
                f"Durability: {durability}",
                f"Source: {source_label}",
                f"Item Id: {item_id}",
                f"TemplateId: {template_id}",
            ]
            return "\n".join(lines)
        # Container header row (Equipment mode group).
        container_key = self.inventory_container_refs.get(row_id)
        if container_key is not None:
            try:
                source, owner_id = container_key.split(":", 1)
            except ValueError:
                return ""
            container_item = self._find_inventory_item(source, owner_id)
            template_id = (container_item.get("TemplateId") or "") if container_item else ""
            meta = self._catalog_meta(template_id) if template_id else {}
            display_name = _catalog_display_name(meta) if meta else (container_item.get("TemplateId") if container_item else "container")
            type_label = self._row_visual_category(meta) if meta else ""
            cat_id = meta.get("CategoryID", "") if meta else ""
            cat_name = self._category_name(cat_id)
            sub_id = meta.get("SubcategoryID", "") if meta else ""
            w = meta.get("Width", "") if meta else ""
            h = meta.get("Height", "") if meta else ""
            size = f"{w}x{h}".strip("x") if (w or h) else ""
            # Count direct children in this container.
            try:
                src_items = get_items_list(self._inventory_data, source) if self._inventory_data is not None else []
                child_count = sum(1 for it in src_items if it.get("ParentId") == owner_id)
            except (KeyError, AttributeError):
                child_count = 0
            lines = [
                f"Container: {display_name}",
                f"Type: {type_label or 'Container'}",
                f"Category: {cat_name or '(blank)'} (id {cat_id or '?'})  Sub: {sub_id or '(blank)'}",
                f"Grid Size: {size or '(blank)'}",
                f"Direct items: {child_count}",
                f"Source: {source}",
                f"Owner Id: {owner_id}",
                f"TemplateId: {template_id or '(none)'}",
                "Expand/collapse to show or hide its items.",
            ]
            return "\n".join(lines)
        return ""

    def _find_inventory_item(self, source: str, item_id: str) -> Optional[dict]:
        if not source or not item_id:
            return None
        data = getattr(self, "_inventory_data", None)
        if data is None:
            return None
        try:
            for it in get_items_list(data, source):
                if it.get("Id") == item_id:
                    return it
        except (KeyError, AttributeError):
            return None
        return None

    def _inventory_search_text(
        self,
        *,
        item: dict,
        name: str,
        source: str,
        qty: str,
        position: str,
        size: str,
        condition: str,
        durability: str,
    ) -> str:
        template_id = item.get("TemplateId", "")
        meta = self._catalog_meta(template_id)
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        parts = [
            name,
            f"name={name}",
            source,
            f"source={source}",
            qty,
            position,
            size,
            condition,
            durability,
        ]
        for key, value in meta.items():
            self._add_search_field(parts, key, value)
        visual_category = self._row_visual_category(meta)
        parts.extend([
            f"categoryid={meta.get('CategoryID', '')}",
            f"subcategoryid={meta.get('SubcategoryID', '')}",
            f"category={visual_category}",
            f"visualcategory={visual_category}",
            f"vcat={visual_category}",
        ])
        for key, value in ad.items():
            if self._is_guid_search_field(key) or self._is_guid_search_value(value):
                continue
            parts.append(f"{key}={value}")
        return "\n".join(str(part or "") for part in parts)

    @staticmethod
    def _format_stat_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return str(value)

    def _item_stat_pair(self, item: dict, current_key: str, max_key: str) -> str:
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        current = ad.get(current_key)
        maximum = ad.get(max_key)
        if current is None and maximum is None:
            return ""
        if current is None:
            current = maximum
        if maximum is None:
            return self._format_stat_value(current)
        return f"{self._format_stat_value(current)} / {self._format_stat_value(maximum)}"

    def _item_condition_value(self, item: dict) -> str:
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        current = ad.get("Condition_d")
        cap = ad.get("Condition_mt")
        if current is None and cap is None:
            return ""
        if current is None:
            current = cap
        try:
            current_float = float(current)
            percent = max(0.0, min(999.0, (current_float / CONDITION_FULL_VALUE) * 100.0))
        except (TypeError, ValueError):
            return self._format_stat_value(current)
        text = f"{self._format_stat_value(current_float)} / {self._format_stat_value(CONDITION_FULL_VALUE)} ({percent:.0f}%)"
        try:
            cap_float = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            cap_float = None
        if cap_float is not None and abs(cap_float - CONDITION_FULL_VALUE) > 0.0001:
            text += f" cap {self._format_stat_value(cap_float)}"
        return text

    def _item_durability_value(self, item: dict) -> str:
        ad = ((item.get("AdditionalData") or {}).get("_data") or {})
        current = ad.get("DurabilityComponent_durability")
        maximum = ad.get("DurabilityComponent_md")
        if current is None and maximum is None:
            # Brand-new full-charge items (e.g. MaRS, repair kits) often omit
            # the durability field entirely; the game treats "missing" as full.
            # Surface the implied full state when we know the template's cap.
            uses_max = get_use_count_max(item)
            if uses_max is not None:
                formatted = self._format_stat_value(uses_max)
                return f"uses {formatted} / {formatted}"
            return ""
        if maximum is None:
            uses_max = get_use_count_max(item)
            if uses_max is not None:
                return f"uses {self._format_stat_value(current)} / {self._format_stat_value(uses_max)}"
            return f"uses {self._format_stat_value(current)}"
        return self._item_stat_pair(item, "DurabilityComponent_durability", "DurabilityComponent_md")

    @staticmethod
    def _item_position(item: dict) -> str:
        pos = item.get("Position") or {}
        if not pos:
            return ""
        i = pos.get("I", 0)
        j = pos.get("J", 0)
        return f"{i},{j}"

    @staticmethod
    def _infer_grid_width_from_children(items: list[dict], owner_id: str, dims: dict[str, tuple[int, int]]) -> int:
        max_width = 0
        for item in items:
            if item.get("ParentId") != owner_id:
                continue
            pos = item.get("Position") or {}
            try:
                i = int(pos.get("I", 0))
                j = int(pos.get("J", 0))
            except (TypeError, ValueError):
                continue
            if i < 0 or j < 0:
                continue
            w, _h = dims.get(item.get("TemplateId", ""), (1, 1))
            max_width = max(max_width, i + w)
        return max_width or 10

    def _refresh_inventory_view(self) -> None:
        if not hasattr(self, "inventory_tree"):
            return
        self.inventory_tree.delete(*self.inventory_tree.get_children())
        self.inventory_item_refs.clear()
        self.inventory_repair_refs.clear()
        self.inventory_container_refs.clear()
        self._inventory_data = None

        save_path = Path(self.save_var.get())
        if not save_path.exists():
            self.status_var.set("Save file not found; inventory view is empty.")
            return

        try:
            data = load_save(save_path)
            names = load_template_names(Path(self.csv_var.get()))
            dims = load_template_dims(Path(self.csv_var.get()))
            observed_stack_max = self._observed_stack_max(data)
            if not self.containers:
                self.containers = discover_containers(data, names)
                self.containers.extend(self._synthesize_empty_containers(data, names, self.containers))
            containers = self._inventory_containers_to_show()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load inventory", str(exc))
            return

        # Cache the loaded save dict so the tree tooltip can resolve item ids
        # without reloading the file on every hover.
        self._inventory_data = data

        total_items = 0
        filter_query = self.inventory_filter_var.get().strip() if hasattr(self, "inventory_filter_var") else ""
        group_by_container = bool(self.group_inventory_containers_var.get()) if hasattr(self, "group_inventory_containers_var") else True
        view_mode = self.inventory_view_mode_var.get() if hasattr(self, "inventory_view_mode_var") else INVENTORY_VIEW_MODES[0]
        if view_mode not in INVENTORY_VIEW_MODES:
            view_mode = INVENTORY_VIEW_MODES[0]

        # Build child-count maps once per source for classification + empty-aware
        # rendering. An item with 1+ children is treated as Equipment (container).
        child_count_by_source: dict[str, dict[str, int]] = {}
        for source in CURRENT_INVENTORY_SOURCES:
            try:
                counts: dict[str, int] = {}
                for it in get_items_list(data, source):
                    pid = it.get("ParentId")
                    if pid:
                        counts[pid] = counts.get(pid, 0) + 1
                child_count_by_source[source] = counts
            except KeyError:
                child_count_by_source[source] = {}

        def _row_source_label(source: str, template_id: str, item_id: str) -> str:
            has_children = child_count_by_source.get(source, {}).get(item_id, 0) > 0
            return self._classify_inventory_row(template_id, has_children)

        if view_mode == "Items":
            # Items mode: show all non-container items. Items whose parent is
            # another non-container item in the save are nested under that
            # parent (e.g. weapon parts appear under their weapon receiver).
            # This relies only on the ParentId chain, not category IDs, so it
            # works even when templates are unknown to the catalog.
            self.inventory_tree.heading("#0", text="Item")

            items_by_id: dict[str, tuple[str, dict]] = {}
            for source in CURRENT_INVENTORY_SOURCES:
                try:
                    for it in get_items_list(data, source):
                        iid = it.get("Id")
                        if iid:
                            items_by_id[iid] = (source, it)
                except KeyError:
                    continue

            # Build full parent→children map across all tracked items.
            children_by_parent: dict[str, list[str]] = {}
            for iid_key, (_src, it_val) in items_by_id.items():
                pid = it_val.get("ParentId") or ""
                if pid:
                    children_by_parent.setdefault(pid, []).append(iid_key)

            def _parent_is_tracked_non_container(item: dict) -> bool:
                """True when this item is a component of another tracked item
                (e.g. weapon attachment). Container parents are OK – those
                items should still appear at top level in Items mode."""
                pid = item.get("ParentId") or ""
                if not pid:
                    return False
                parent_entry = items_by_id.get(pid)
                if parent_entry is None:
                    return False
                return self._item_role(parent_entry[1].get("TemplateId", "")) != "container"

            def _row_search_text_items(source: str, item: dict, name: str) -> str:
                template_id = item.get("TemplateId", "")
                w_dim, h_dim = dims.get(template_id, (1, 1))
                return self._inventory_search_text(
                    item=item,
                    name=name,
                    source=_row_source_label(source, template_id, item.get("Id", "")),
                    qty=self._item_stack_value(item, observed_stack_max),
                    position=self._item_position(item),
                    size=f"{w_dim}x{h_dim}",
                    condition=self._item_condition_value(item),
                    durability=self._item_durability_value(item),
                )

            def _row_matches_filter_items(source: str, item: dict, name: str) -> bool:
                if not filter_query:
                    return True
                return self._matches_terms(_row_search_text_items(source, item, name), filter_query)

            def _any_descendant_matches(owner_id: str) -> bool:
                if not filter_query:
                    return True
                stack = list(children_by_parent.get(owner_id, []))
                visited: set[str] = set()
                while stack:
                    cur = stack.pop()
                    if not cur or cur in visited:
                        continue
                    visited.add(cur)
                    entry = items_by_id.get(cur)
                    if not entry:
                        continue
                    d_src, d_item = entry
                    d_tid = d_item.get("TemplateId", "")
                    d_name = names.get(d_tid) or d_tid[:8] or "<unknown>"
                    if _row_matches_filter_items(d_src, d_item, d_name):
                        return True
                    stack.extend(children_by_parent.get(cur, []))
                return False

            def _insert_leaf_row_items(parent_tree_id: str, source: str, item: dict) -> Optional[str]:
                template_id = item.get("TemplateId", "")
                name = names.get(template_id) or template_id[:8] or "<unknown>"
                w_dim, h_dim = dims.get(template_id, (1, 1))
                qty = self._item_stack_value(item, observed_stack_max)
                position = self._item_position(item)
                size = f"{w_dim}x{h_dim}"
                item_condition = self._item_condition_value(item)
                item_durability = self._item_durability_value(item)
                meta = self._catalog_meta(template_id)
                category = self._category_name(meta.get("CategoryID", ""))
                type_label = self._row_visual_category(meta) if meta else ""
                source_label = _row_source_label(source, template_id, item.get("Id", ""))
                row_values = (
                    qty, position, size, type_label, category,
                    item_condition, item_durability, source_label,
                    item.get("Id", ""), template_id,
                )
                tag = self._color_tag(meta.get("CategoryID", ""))
                row_options: dict = {"text": name, "values": row_values, "tags": (tag,) if tag else ()}
                icon = self._tree_icon(template_id, name)
                if icon is not None:
                    row_options["image"] = icon
                row_id = self.inventory_tree.insert(parent_tree_id, "end", **row_options)
                iid_val = item.get("Id") or ""
                if iid_val:
                    self.inventory_item_refs[row_id] = (source, iid_val, name)
                    self.inventory_repair_refs[row_id] = (source, iid_val, name)
                return row_id

            def _insert_descendants_recursive(parent_tree_id: str, owner_id: str) -> int:
                count = 0
                child_ids = children_by_parent.get(owner_id, [])
                for child_id in sorted(
                    child_ids,
                    key=lambda cid: (names.get(items_by_id[cid][1].get("TemplateId", ""), "") or "").lower(),
                ):
                    if child_id not in items_by_id:
                        continue
                    src, child = items_by_id[child_id]
                    row_id = _insert_leaf_row_items(parent_tree_id, src, child)
                    if row_id is None:
                        continue
                    count += 1
                    count += _insert_descendants_recursive(row_id, child_id)
                return count

            weapons_first = bool(self.inventory_weapons_first_var.get()) if hasattr(self, "inventory_weapons_first_var") else False
            top_level: list[tuple[int, str, str, dict]] = []
            for iid_key, (source, item) in items_by_id.items():
                template_id = item.get("TemplateId", "")
                role = self._item_role(template_id)
                if role == "container":
                    continue  # containers belong in Equipment mode
                if _parent_is_tracked_non_container(item):
                    continue  # nested component; shown under its parent
                name = names.get(template_id) or template_id[:8] or "<unknown>"
                has_children = bool(children_by_parent.get(iid_key))
                # "Expandable on top" groups expandable items (group 0) before
                # flat ones (group 1). When the option is off, all share group 0.
                group_key = 0 if (has_children and weapons_first) else (1 if weapons_first else 0)
                top_level.append((group_key, name.lower(), source, item))

            for _gk, _name_key, source, item in sorted(top_level, key=lambda t: (t[0], t[1])):
                template_id = item.get("TemplateId", "")
                iid = item.get("Id") or ""
                name = names.get(template_id) or template_id[:8] or "<unknown>"
                if filter_query:
                    if not _row_matches_filter_items(source, item, name) and not _any_descendant_matches(iid):
                        continue
                row_id = _insert_leaf_row_items("", source, item)
                if row_id is None:
                    continue
                descendant_count = _insert_descendants_recursive(row_id, iid)
                if descendant_count:
                    self.inventory_tree.item(
                        row_id,
                        text=f"{name} ({descendant_count})",
                        open=self.inventory_container_open_state.get(f"{source}:{iid}", True),
                    )
                    self.inventory_container_refs[row_id] = f"{source}:{iid}"
                total_items += 1 + descendant_count

            self._restore_treeview_sort(self.inventory_tree)
            if weapons_first:
                # _restore_treeview_sort re-sorts top-level rows by the active
                # column, wiping our expandable-first grouping. Re-apply it now
                # while preserving the column-sorted order within each group.
                top_rows = list(self.inventory_tree.get_children(""))
                expandable = [r for r in top_rows if r in self.inventory_container_refs]
                flat = [r for r in top_rows if r not in self.inventory_container_refs]
                for index, row_id in enumerate(expandable + flat):
                    self.inventory_tree.move(row_id, "", index)
            self.status_var.set(
                f"Inventory view: {total_items} row(s) (Items mode; items with parts are expandable)."
            )
            return

        self.inventory_tree.heading("#0", text="Item / container" if group_by_container else "Item")

        # Equipment view shows only TRUE containers as expandable groups. Items
        # with children that aren't actually containers (weapon roots whose
        # parts hang off them, weapon-part chains like receiver->barrel->FH)
        # are pruned here so a gun reads as a single row instead of three
        # expandable groups.
        def _is_kept_container(c: Container) -> bool:
            # Always keep the inventory root (backpack) and shelter root.
            if c.label in ("Backpack", "Shelter"):
                return True
            return self._item_role(c.template_id or "") == "container"

        kept_containers = [c for c in containers if _is_kept_container(c)]
        kept_container_ids = {c.owner_item_id for c in kept_containers}

        for container in kept_containers:
            items = get_items_list(data, container.source)
            all_children = [it for it in items if it.get("ParentId") == container.owner_item_id]
            container_item = next((it for it in items if it.get("Id") == container.owner_item_id), None)
            dims_label = f"{container.grid_width}x{container.grid_height}" if container.grid_width else ""
            condition = self._item_condition_value(container_item or {})
            durability = self._item_durability_value(container_item or {})
            container_meta = self._catalog_meta(container.template_id or "")
            container_category = self._category_name(container_meta.get("CategoryID", ""))
            container_type = self._row_visual_category(container_meta) if container_meta else ""
            container_search = self._inventory_search_text(
                item=container_item or {"Id": container.owner_item_id, "TemplateId": container.template_id or ""},
                name=container.label,
                source=INVENTORY_SOURCE_EQUIPMENT_LABEL,
                qty="",
                position="",
                size=dims_label,
                condition=condition,
                durability=durability,
            )
            container_matches = bool(filter_query) and self._matches_terms(container_search, filter_query)
            display_children: list[dict] = []
            for item in all_children:
                template_id = item.get("TemplateId", "")
                search_name = names.get(template_id) or "<unknown>"
                w, h = dims.get(template_id, (1, 1))
                qty = self._item_stack_value(item, observed_stack_max)
                position = self._item_position(item)
                size = f"{w}x{h}"
                item_condition = self._item_condition_value(item)
                item_durability = self._item_durability_value(item)
                child_classification = _row_source_label(container.source, template_id, item.get("Id", ""))
                search_text = self._inventory_search_text(
                    item=item,
                    name=search_name,
                    source=child_classification,
                    qty=qty,
                    position=position,
                    size=size,
                    condition=item_condition,
                    durability=item_durability,
                )
                if not filter_query or container_matches or self._matches_terms(search_text, filter_query):
                    display_children.append(item)
            if filter_query and not container_matches and not display_children:
                continue
            total_items += len(display_children)
            container_key = f"{container.source}:{container.owner_item_id}"
            if group_by_container:
                group_options = {
                    "text": f"{container.label} ({len(display_children)} of {len(all_children)} items)" if filter_query else f"{container.label} ({len(all_children)} items)",
                    "values": ("", "", dims_label, container_type, container_category, condition, durability, INVENTORY_SOURCE_EQUIPMENT_LABEL, container.owner_item_id, container.template_id or ""),
                    "open": self.inventory_container_open_state.get(container_key, True),
                }
                group_icon = self._tree_icon(container.template_id or "", container.label)
                if group_icon is not None:
                    group_options["image"] = group_icon
                parent_id = self.inventory_tree.insert("", "end", **group_options)
                self.inventory_container_refs[parent_id] = container_key
                if container_item and container_item.get("Id"):
                    self.inventory_repair_refs[parent_id] = (container.source, container_item["Id"], container.label)
            else:
                parent_id = ""
            for item in sorted(display_children, key=lambda it: (self._item_position(it), names.get(it.get("TemplateId", ""), ""))):
                template_id = item.get("TemplateId", "")
                name = names.get(template_id) or template_id[:8] or "<unknown>"
                w, h = dims.get(template_id, (1, 1))
                qty = self._item_stack_value(item, observed_stack_max)
                condition = self._item_condition_value(item)
                durability = self._item_durability_value(item)
                child_meta = self._catalog_meta(template_id)
                category = self._category_name(child_meta.get("CategoryID", ""))
                child_type = self._row_visual_category(child_meta) if child_meta else ""
                child_source_label = _row_source_label(container.source, template_id, item.get("Id", ""))
                row_values = (
                    qty,
                    self._item_position(item),
                    f"{w}x{h}",
                    child_type,
                    category,
                    condition,
                    durability,
                    child_source_label if group_by_container else f"{child_source_label}: {container.label}",
                    item.get("Id", ""),
                    template_id,
                )
                child_tag = self._color_tag(child_meta.get("CategoryID", "") if child_meta else "")
                row_options = {"text": name, "values": row_values, "tags": (child_tag,) if child_tag else ()}
                row_icon = self._tree_icon(template_id, name)
                if row_icon is not None:
                    row_options["image"] = row_icon
                row_id = self.inventory_tree.insert(
                    parent_id,
                    "end",
                    **row_options,
                )
                if item.get("Id"):
                    self.inventory_item_refs[row_id] = (container.source, item["Id"], name)
                    self.inventory_repair_refs[row_id] = (container.source, item["Id"], name)

        # ---- After containers, render top-level orphans (weapons + loose items) ----
        # These are items that don't belong inside any visible container expand:
        # equipped weapons, loose tools/aid kits parented to the player slot, etc.
        # Weapon parts (cat 20) attached to a weapon are intentionally hidden in
        # Equipment view so the gun reads as one row.
        orphans_seen: set[str] = set()
        orphan_rows: list[tuple[str, str, dict]] = []  # (sort_key, source, item)
        for source in CURRENT_INVENTORY_SOURCES:
            try:
                src_items = get_items_list(data, source)
            except KeyError:
                continue
            for item in src_items:
                iid = item.get("Id") or ""
                if not iid or iid in orphans_seen:
                    continue
                if iid in kept_container_ids:
                    continue  # already shown as its own top-level expand
                parent_id_val = item.get("ParentId") or ""
                if parent_id_val in kept_container_ids:
                    continue  # already shown as a child inside its container
                template_id = item.get("TemplateId", "")
                role = self._item_role(template_id)
                if role == "weapon_part":
                    # Skip parts attached to a weapon root (any weapon ancestor).
                    visited: set[str] = set()
                    cur_id = parent_id_val
                    has_weapon_anc = False
                    while cur_id and cur_id not in visited:
                        visited.add(cur_id)
                        anc_entry = next(
                            (
                                (s, it_)
                                for s in CURRENT_INVENTORY_SOURCES
                                for it_ in get_items_list(data, s)
                                if it_.get("Id") == cur_id
                            ),
                            None,
                        )
                        if not anc_entry:
                            break
                        _s, anc_item = anc_entry
                        if self._item_role(anc_item.get("TemplateId", "")) == "weapon":
                            has_weapon_anc = True
                            break
                        cur_id = anc_item.get("ParentId") or ""
                    if has_weapon_anc:
                        continue  # attached part; hidden in Equipment view
                # role is weapon, item, or loose weapon_part: surface at top level.
                name = names.get(template_id) or template_id[:8] or "<unknown>"
                orphans_seen.add(iid)
                orphan_rows.append((name.lower(), source, item))

        orphan_displayed = 0
        for _key, source, item in sorted(orphan_rows, key=lambda t: t[0]):
            template_id = item.get("TemplateId", "")
            name = names.get(template_id) or template_id[:8] or "<unknown>"
            w, h = dims.get(template_id, (1, 1))
            qty = self._item_stack_value(item, observed_stack_max)
            position = self._item_position(item)
            size = f"{w}x{h}"
            item_condition = self._item_condition_value(item)
            item_durability = self._item_durability_value(item)
            category = self._category_name(self._catalog_meta(template_id).get("CategoryID", ""))
            source_label = _row_source_label(source, template_id, item.get("Id", ""))
            search_text = self._inventory_search_text(
                item=item,
                name=name,
                source=source_label,
                qty=qty,
                position=position,
                size=size,
                condition=item_condition,
                durability=item_durability,
            )
            if filter_query and not self._matches_terms(search_text, filter_query):
                continue
            orphan_meta = self._catalog_meta(template_id)
            orphan_type = self._row_visual_category(orphan_meta) if orphan_meta else ""
            orphan_tag = self._color_tag(orphan_meta.get("CategoryID", "") if orphan_meta else "")
            row_values = (
                qty,
                position,
                size,
                orphan_type,
                category,
                item_condition,
                item_durability,
                source_label,
                item.get("Id", ""),
                template_id,
            )
            row_options = {"text": name, "values": row_values, "tags": (orphan_tag,) if orphan_tag else ()}
            icon = self._tree_icon(template_id, name)
            if icon is not None:
                row_options["image"] = icon
            row_id = self.inventory_tree.insert("", "end", **row_options)
            iid = item.get("Id") or ""
            if iid:
                self.inventory_item_refs[row_id] = (source, iid, name)
                self.inventory_repair_refs[row_id] = (source, iid, name)
            orphan_displayed += 1
            total_items += 1

        self._restore_treeview_sort(self.inventory_tree)
        if group_by_container:
            self.status_var.set(
                f"Inventory view: {total_items} item(s) across {len(kept_containers)} container(s)"
                + (f" + {orphan_displayed} top-level item(s)." if orphan_displayed else ".")
            )
        else:
            self.status_var.set(f"Inventory view: {total_items} item(s) in a flat sortable list.")

    def _collapse_all_inventory_containers(self) -> None:
        if not hasattr(self, "inventory_tree"):
            return
        for row_id in self.inventory_tree.get_children(""):
            self.inventory_tree.item(row_id, open=False)
            container_key = self.inventory_container_refs.get(row_id)
            if container_key is not None:
                self.inventory_container_open_state[container_key] = False
        self.status_var.set("Collapsed all inventory containers.")

    def _expand_all_inventory_containers(self) -> None:
        if not hasattr(self, "inventory_tree"):
            return
        for row_id in self.inventory_tree.get_children(""):
            self.inventory_tree.item(row_id, open=True)
            container_key = self.inventory_container_refs.get(row_id)
            if container_key is not None:
                self.inventory_container_open_state[container_key] = True
        self.status_var.set("Expanded all inventory containers.")

    def _on_inventory_tree_open(self, _event: object = None) -> None:
        row_id = self.inventory_tree.focus()
        container_key = self.inventory_container_refs.get(row_id)
        if container_key is not None:
            self.inventory_container_open_state[container_key] = True

    def _on_inventory_tree_close(self, _event: object = None) -> None:
        row_id = self.inventory_tree.focus()
        container_key = self.inventory_container_refs.get(row_id)
        if container_key is not None:
            self.inventory_container_open_state[container_key] = False

    def _on_inventory_delete_key(self, _event: object = None) -> str:
        """Pressing Delete (or numpad Delete) in the inventory tree triggers the
        same delete flow as the toolbar button. Returns "break" so the keystroke
        is not propagated to other handlers."""
        self._delete_selected_inventory_items()
        return "break"

    def _ask_split_quantity(self, item_name: str, current_quantity: int) -> Optional[int]:
        if current_quantity < 2:
            return None
        dialog = tk.Toplevel(self)
        dialog.title("Split stack")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        selected = tk.IntVar(value=max(1, current_quantity // 2))
        result: dict[str, Optional[int]] = {"value": None}

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Split {item_name}", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"Current stack: {current_quantity}").pack(anchor="w", pady=(2, 8))

        value_label = ttk.Label(frame)
        value_label.pack(anchor="w")

        def update_label(_value: object = None) -> None:
            amount = int(selected.get())
            value_label.configure(
                text=f"Move {amount} to a new stack; original keeps {current_quantity - amount}."
            )

        slider = tk.Scale(
            frame,
            from_=1,
            to=current_quantity - 1,
            orient="horizontal",
            variable=selected,
            resolution=1,
            showvalue=True,
            length=360,
            command=update_label,
        )
        self._tooltip(slider, "Choose how many items to move into the new stack. The original stack keeps the remainder.")
        slider.pack(fill="x", pady=(4, 8))
        update_label()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def accept() -> None:
            result["value"] = int(selected.get())
            dialog.destroy()

        def cancel() -> None:
            result["value"] = None
            dialog.destroy()

        split_button = self._tooltip(
            ttk.Button(buttons, text="Split", command=accept),
            "Create the new stack with the selected quantity and write a backed-up save.",
        )
        split_button.pack(side="right")
        cancel_button = self._tooltip(
            ttk.Button(buttons, text="Cancel", command=cancel),
            "Close this dialog without changing the selected stack.",
        )
        cancel_button.pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: cancel())

        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_reqwidth()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result["value"]

    def _split_selected_inventory_stack(self) -> None:
        selected = [row for row in self.inventory_tree.selection() if row in self.inventory_item_refs]
        if len(selected) != 1:
            messagebox.showinfo("Select one stack", "Select exactly one stackable item row to split. Container rows are ignored.")
            return

        source, item_id, name = self.inventory_item_refs[selected[0]]
        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return

        try:
            data = load_save(save_path)
            items = get_items_list(data, source)
            item = next((it for it in items if it.get("Id") == item_id), None)
            if item is None:
                raise ValueError(f"Could not find selected item Id {item_id!r}.")
            ad = ((item.get("AdditionalData") or {}).get("_data") or {})
            if "StackableComponent_quantity" not in ad:
                raise ValueError("Selected item does not store a stack quantity and cannot be split.")
            current_quantity = int(ad["StackableComponent_quantity"])
            if current_quantity < 2:
                raise ValueError("Selected stack must have at least 2 items to split.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Cannot split stack", f"{type(exc).__name__}: {exc}")
            return

        split_quantity = self._ask_split_quantity(name, current_quantity)
        if split_quantity is None:
            return

        try:
            csv_path = Path(self.csv_var.get())
            dims = load_template_dims(csv_path)
            names = load_template_names(csv_path)
            containers = discover_containers(data, names)
            parent_id = item.get("ParentId")
            container = next(
                (c for c in containers if c.source == source and c.owner_item_id == parent_id),
                None,
            )
            grid_width = (
                container.grid_width
                if container and container.grid_width
                else self._infer_grid_width_from_children(items, parent_id, dims)
            )
            info = split_stack_item(
                data,
                source=source,
                item_id=item_id,
                split_quantity=split_quantity,
                dims=dims,
                grid_width=grid_width,
            )
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Split failed", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        pos_i, pos_j = info["position"]
        self.log.insert(
            "end",
            f"Split stack: {name} moved {info['new_quantity']} to new stack "
            f"{info['new_id']} at ({pos_i},{pos_j}); original now {info['original_quantity']}.\n",
        )
        self.log.see("end")
        self._refresh_containers()
        self.status_var.set(
            f"Split {name}: new stack {info['new_quantity']}, original {info['original_quantity']}."
        )

    def _move_selected_inventory_items(self) -> None:
        """Move the currently selected inventory rows into a chosen container."""
        if not hasattr(self, "inventory_tree"):
            return
        selected = self.inventory_tree.selection()
        # Collect anchor rows: leaf items + container-header rows that are real
        # items (weapons, vests, backpacks, kits). Pure root rows like
        # "Backpack" / "Shelter" are not in inventory_repair_refs and so are
        # skipped.
        ref_map = self.inventory_repair_refs
        rows: list[tuple[str, str, str]] = []
        seen_ids: set[tuple[str, str]] = set()
        for row in selected:
            entry = ref_map.get(row)
            if entry is None:
                continue
            key = (entry[0], entry[1])
            if key in seen_ids:
                continue
            seen_ids.add(key)
            rows.append(entry)
        if not rows:
            messagebox.showinfo(
                "No movable items selected",
                "Select one or more item rows (or a container header row such as a weapon, vest, or kit) first. The Backpack and Shelter root rows cannot be moved.",
            )
            return

        destinations = list(self.containers)
        if not destinations:
            messagebox.showinfo(
                "No destinations available",
                "Could not list any containers. Refresh containers and try again.",
            )
            return

        picked = self._ask_move_destination(rows, destinations)
        if picked is None:
            return
        dest_container = picked

        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return

        csv_path = Path(self.csv_var.get())
        try:
            dims = load_template_dims(csv_path) if csv_path.exists() else {}
            names = load_template_names(csv_path) if csv_path.exists() else {}
        except OSError as exc:
            messagebox.showerror("CSV unavailable", f"{type(exc).__name__}: {exc}")
            return

        try:
            data = load_save(save_path)
            info = move_items_to_container(
                data,
                [item_id for _src, item_id, _name in rows],
                dest_source=dest_container.source,
                dest_owner_id=dest_container.owner_item_id,
                dest_grid_width=dest_container.grid_width,
                dest_grid_height=dest_container.grid_height,
                dims=dims,
                names=names,
            )
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep())
        except ValueError as exc:
            messagebox.showwarning("Not enough room", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Move failed", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        anchors = int(info.get("moved_anchor_count", 0) or 0)
        total = int(info.get("moved_total_count", 0) or 0)
        carried = total - anchors
        carry_txt = f" (+ {carried} nested item(s) carried along)" if carried else ""
        dest_label = self._dest_label(dest_container)
        self.log.insert(
            "end",
            f"Moved {anchors} item(s) to {dest_label}{carry_txt}.\n",
        )
        self.log.see("end")
        self._refresh_containers()
        self.status_var.set(f"Moved {anchors} item(s) to {dest_label}.")

    def _ask_move_destination(
        self,
        rows: list[tuple[str, str, str]],
        destinations: list[Container],
    ) -> Optional[Container]:
        """Prompt for a destination container. Returns the chosen Container or None."""
        csv_path = Path(self.csv_var.get())

        # Compute per-destination free-cell estimates for the dropdown labels.
        data_for_estimates: Optional[dict] = None
        dims_for_estimates: dict[str, tuple[int, int]] = {}
        if csv_path.exists():
            try:
                dims_for_estimates = load_template_dims(csv_path)
            except OSError:
                dims_for_estimates = {}
        save_path = Path(self.save_var.get())
        if save_path.exists():
            try:
                data_for_estimates = load_save(save_path)
            except Exception:  # noqa: BLE001
                data_for_estimates = None

        def _free_summary(c: Container) -> str:
            if c.grid_width and c.grid_height:
                used = 0
                if data_for_estimates is not None:
                    try:
                        items = get_items_list(data_for_estimates, c.source)
                    except KeyError:
                        items = []
                    occ_cells: set[tuple[int, int]] = set()
                    for it in items:
                        if it.get("ParentId") != c.owner_item_id:
                            continue
                        pos = it.get("Position") or {}
                        try:
                            ii = int(pos.get("I", 0))
                            jj = int(pos.get("J", 0))
                        except (TypeError, ValueError):
                            continue
                        if ii < 0 or jj < 0:
                            continue
                        w, h = dims_for_estimates.get(it.get("TemplateId", ""), (1, 1))
                        for di in range(w):
                            for dj in range(h):
                                occ_cells.add((ii + di, jj + dj))
                    used = len(occ_cells)
                total = c.grid_width * c.grid_height
                return f"{total - used}/{total} free"
            return "size unknown"

        label_to_container: dict[str, Container] = {}
        labels: list[str] = []
        for c in destinations:
            base = self._dest_label(c)
            label = f"{base}   [{_free_summary(c)}]"
            if label in label_to_container:
                label = f"{label} #{len(labels)}"
            label_to_container[label] = c
            labels.append(label)

        dialog = tk.Toplevel(self)
        dialog.title("Move selected items")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.minsize(520, 360)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"Move {len(rows)} selected item(s) to:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        choice_var = tk.StringVar(value=labels[0])
        combo = ttk.Combobox(
            frame, textvariable=choice_var, state="readonly", values=labels, width=72
        )
        combo.pack(fill="x", pady=(6, 10))

        ttk.Label(
            frame,
            text="Items being moved (nested contents travel with their container):",
        ).pack(anchor="w")
        preview_frame = ttk.Frame(frame)
        preview_frame.pack(fill="both", expand=True, pady=(2, 8))
        preview = tk.Listbox(preview_frame, height=10, activestyle="none")
        preview_scroll = ttk.Scrollbar(
            preview_frame, orient="vertical", command=preview.yview
        )
        preview.configure(yscrollcommand=preview_scroll.set)
        preview.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        source_labels = {
            "inventory": "Inv",
            "equipment": "Equip",
            "shelter": "Shelter",
        }
        for source, _item_id, name in rows:
            preview.insert("end", f"[{source_labels.get(source, source)}]  {name}")

        result: dict[str, Optional[Container]] = {"value": None}

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x")

        def _ok() -> None:
            chosen = label_to_container.get(choice_var.get())
            if chosen is None:
                return
            result["value"] = chosen
            dialog.destroy()

        def _cancel() -> None:
            dialog.destroy()

        ttk.Button(button_row, text="Cancel", command=_cancel).pack(side="right")
        ttk.Button(button_row, text="Move", command=_ok).pack(side="right", padx=(0, 6))

        dialog.bind("<Escape>", lambda _e: _cancel())
        dialog.bind("<Return>", lambda _e: _ok())
        combo.focus_set()
        self.wait_window(dialog)

        return result["value"]

    def _delete_selected_inventory_items(self) -> None:
        selected = self.inventory_tree.selection()
        rows = [self.inventory_item_refs[row] for row in selected if row in self.inventory_item_refs]
        if not rows:
            messagebox.showinfo("No items selected", "Select one or more item rows first. Container header rows are ignored.")
            return

        preview = "\n".join(f"• {name}" for _source, _item_id, name in rows[:10])
        if len(rows) > 10:
            preview += f"\n… and {len(rows) - 10} more"
        if not messagebox.askyesno(
            "Delete selected items?",
            f"Remove {len(rows)} selected item(s) from the save?\n\n{preview}\n\nA timestamped backup will be created first.",
        ):
            return

        save_path = Path(self.save_var.get())
        try:
            data = load_save(save_path)
            removed = remove_items_by_ids(data, {item_id for _source, item_id, _name in rows})
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Delete failed", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        self.log.insert("end", f"Removed {removed} item(s).\n")
        self.log.see("end")
        self._refresh_containers()
        self.status_var.set(f"Removed {removed} item(s).")

    def _repair_selected_inventory_items(self) -> None:
        selected = self.inventory_tree.selection()
        rows = [self.inventory_repair_refs[row] for row in selected if row in self.inventory_repair_refs]
        if not rows:
            messagebox.showinfo(
                "No repairable rows selected",
                "Select one or more item rows first. Container headers work too when the header is a real item, such as a weapon, vest, backpack, or kit.",
            )
            return

        preview = "\n".join(f"• {name}" for _source, _item_id, name in rows[:10])
        if len(rows) > 10:
            preview += f"\n… and {len(rows) - 10} more"
        if not messagebox.askyesno(
            "Set selected items to 100%?",
            "Set condition/durability/uses to full for selected item(s)?\n"
            "Stack quantities will also be topped off when a max stack size is known.\n\n"
            f"{preview}\n\n"
            "A timestamped backup will be created if anything changes.",
        ):
            return

        save_path = Path(self.save_var.get())
        try:
            data = load_save(save_path)
            stats = set_items_condition_durability_full(
                data,
                {item_id for _source, item_id, _name in rows},
                top_off_stacks=True,
                stack_max_by_template=self.stack_max_by_template,
            )
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep()) if stats["changed"] else None
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Repair failed", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        self.log.insert(
            "end",
            "Set selected items to 100%: "
            f"matched={stats['matched']} changed={stats['changed']} "
            f"condition={stats['condition']} durability={stats['durability']} uses={stats['uses']} stacks={stats['stacks']} "
            f"skipped_no_stats={stats['skipped_no_stats']} "
            f"skipped_unknown_use_max={stats['skipped_uses_unknown_max']} "
            f"skipped_unknown_stack_max={stats['skipped_stack_unknown_max']}\n",
        )
        self.log.see("end")
        self._refresh_containers()
        if stats["changed"]:
            self.status_var.set(f"Set {stats['changed']} selected item(s) to 100%.")
        elif stats["matched"] and stats["skipped_no_stats"] == stats["matched"]:
            self.status_var.set("Selected item(s) do not store condition/durability stats in the save.")
        else:
            self.status_var.set("Selected item(s) were already full, or their use/stack max is not known yet.")

    def _repair_all_inventory_items(self) -> None:
        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return

        try:
            data = load_save(save_path)
            item_ids: set[str] = set()
            source_counts: dict[str, int] = {}
            for source in CURRENT_INVENTORY_SOURCES:
                try:
                    items = get_items_list(data, source)
                except KeyError:
                    continue
                source_counts[source] = len(items)
                item_ids.update(str(item.get("Id") or "") for item in items if item.get("Id"))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load save", f"{type(exc).__name__}: {exc}")
            return

        if not item_ids:
            messagebox.showinfo("No items found", "No inventory/equipment items were found in this save.")
            return

        counts_text = ", ".join(f"{source}={count}" for source, count in source_counts.items())
        if not messagebox.askyesno(
            "Repair/refill/top off ALL items?",
            "This will scan every non-shelter item in inventory and equipment, then:\n"
            "• set condition to 100%\n"
            "• set durability to max\n"
            "• refill known use-count items\n"
            "• top off stack quantities when a max stack size is known\n\n"
            "Coverage:\n"
            "• Every item in your worn backpack (including items inside sub-containers).\n"
            "• Every item in equipped gear (vest, chest rig, holsters) and items inside them.\n"
            "• Items currently held (loaded magazines, weapon attachments, etc.).\n\n"
            f"Items found: {len(item_ids)} ({counts_text})\n\n"
            "A timestamped backup will be created if anything changes.",
        ):
            return

        try:
            stats = set_items_condition_durability_full(
                data,
                item_ids,
                top_off_stacks=True,
                stack_max_by_template=self.stack_max_by_template,
            )
            backup = write_save(save_path, data, keep_backups=self._current_backup_keep()) if stats["changed"] else None
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Repair/top-off failed", f"{type(exc).__name__}: {exc}")
            return

        if backup is not None:
            self.log.insert("end", f"Backup: {backup.name}\n")
        self.log.insert(
            "end",
            "Repair/refill/top off ALL: "
            f"matched={stats['matched']} changed={stats['changed']} "
            f"condition={stats['condition']} durability={stats['durability']} uses={stats['uses']} stacks={stats['stacks']} "
            f"skipped_no_stats={stats['skipped_no_stats']} "
            f"skipped_unknown_use_max={stats['skipped_uses_unknown_max']} "
            f"skipped_unknown_stack_max={stats['skipped_stack_unknown_max']}\n",
        )
        self.log.see("end")
        self._refresh_containers()
        if stats["changed"]:
            self.status_var.set(
                "Repair/refill/top off ALL complete: "
                f"{stats['changed']} item(s) changed, {stats['stacks']} stack(s) topped off."
                f" ({stats['skipped_no_stats']} item(s) have no repairable stats — that is expected for resources, valuables, etc.)"
            )
        else:
            self.status_var.set(
                "All scanned items were already full"
                f" ({stats['skipped_no_stats']} item(s) have no repairable stats — that is expected for resources, valuables, etc.)."
            )

    def _on_dest_changed(self, _event: object = None) -> None:
        c = self._selected_container()
        if c and c.grid_width:
            self.grid_var.set(str(c.grid_width))
        self.status_var.set(f"Destination: {self.dest_var.get()}")

    def _parse_optional(self, var: tk.StringVar, cast, label: str) -> Optional[object]:
        raw = var.get().strip()
        if not raw:
            return None
        try:
            return cast(raw)
        except ValueError:
            raise ValueError(f"{label!r} is not a valid {cast.__name__}: {raw!r}") from None

    def _do_add(self) -> None:
        selected_rows = self._selected_add_rows()
        if not selected_rows:
            messagebox.showwarning("Pick an item", "Select one or more item rows from the list first.")
            return

        save_path = Path(self.save_var.get())
        if not save_path.exists():
            messagebox.showerror("Save not found", f"{save_path} does not exist.")
            return

        try:
            qty = self._parse_optional(self.qty_var, int, "Quantity")
            count_raw = self.count_var.get().strip() or "1"
            count = int(count_raw)
            condition = self._parse_optional(self.cond_var, float, "Condition")
            durability = self._parse_optional(self.dur_var, float, "Durability")
            grid_raw = self.grid_var.get().strip()
            grid_width = int(grid_raw) if grid_raw else None
            csv_path = Path(self.csv_var.get())
        except ValueError as exc:
            messagebox.showerror("Bad input", str(exc))
            return

        container = self._selected_container()
        if container is None:
            messagebox.showwarning("Pick destination", "Choose a destination container first.")
            return

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                for row in selected_rows:
                    name = row.get("ItemName", "")
                    template_id = row.get("ItemID", "")
                    add_items(
                        save_path,
                        template_id,
                        name,
                        qty=qty,
                        count=count,
                        condition=condition,
                        durability=durability,
                        grid_width=grid_width,
                        csv_path=csv_path,
                        source=container.source,
                        owner_id=container.owner_item_id,
                    )
        except SystemExit as exc:
            output = buf.getvalue()
            if output:
                self.log.insert("end", output + "\n")
                self.log.see("end")
            messagebox.showerror("Add failed", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            output = buf.getvalue()
            if output:
                self.log.insert("end", output + "\n")
                self.log.see("end")
            messagebox.showerror("Add failed", f"{type(exc).__name__}: {exc}")
            return

        output = buf.getvalue()
        self.log.insert("end", output + "\n")
        self.log.see("end")
        keep = self._current_backup_keep()
        if keep is not None:
            deleted = prune_old_backups(save_path, keep=keep)
            if deleted:
                self.log.insert("end", f"Pruned {len(deleted)} old backup(s) (keep last {keep}).\n")
                self.log.see("end")
        if len(selected_rows) == 1:
            self.status_var.set(f"Added {count} x {selected_rows[0].get('ItemName', '')} -> {container.label}")
        else:
            self.status_var.set(f"Added {count} each of {len(selected_rows)} selected item type(s) -> {container.label}")
        self._refresh_containers()
        self._refresh_inventory_view()


def main() -> int:
    app = EditorGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
