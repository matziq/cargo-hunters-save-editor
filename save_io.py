"""Shared I/O helpers for the Cargo Hunters save editor.

Keeps backup/load/save in one place so the CLI (``add_item.py``) and the GUI
(``editor_gui.py``) agree on file format and backup naming.

Containers
----------
The save has three places where items live:

* ``InventoryDto.ItemsContainerDto.Items`` — the single "backpack" item plus
  everything inside it.
* ``EquipmentDto.Items`` — worn gear (vest, sling, pockets, holsters). Each
  top-level item is its own container with its own grid dimensions stored in
  ``AdditionalData._data.BaseComponent_width/_height``.
* ``ShelterItemDto.Container.Items`` — base storage (only accessible at the
  shelter).

A "container" for our purposes is any item that already has at least one child
in its array, plus the well-known roots (backpack / shelter). Each container
is keyed by ``(source, owner_item_id)`` where ``source`` is one of
``"inventory" | "equipment" | "shelter"``.
"""

from __future__ import annotations

import copy
import csv
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional


# ---------- low-level file I/O ----------

def load_save(save_path: Path) -> dict:
    return json.loads(save_path.read_text(encoding="utf-8"))


def write_save(
    save_path: Path,
    data: dict,
    *,
    make_backup: bool = True,
    keep_backups: Optional[int] = None,
) -> Optional[Path]:
    """Write ``data`` to ``save_path``.

    If ``make_backup`` is true, copy the current file to
    ``<name>.<timestamp>.bak`` first. When ``keep_backups`` is a positive int,
    delete the oldest ``.bak`` files for this save so that at most
    ``keep_backups`` remain (the just-created one is always preserved).
    """
    backup: Optional[Path] = None
    if make_backup:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup = save_path.with_name(save_path.name + f".{stamp}.bak")
        shutil.copy2(save_path, backup)
    save_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    if make_backup and keep_backups is not None and keep_backups > 0:
        prune_old_backups(save_path, keep=keep_backups, protect=backup)
    return backup


def list_save_backups(save_path: Path) -> list[Path]:
    """Return all ``<save_path>.<timestamp>.bak`` files newest-first."""
    parent = save_path.parent
    if not parent.exists():
        return []
    prefix = save_path.name + "."
    backups: list[Path] = []
    for entry in parent.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if not name.startswith(prefix) or not name.endswith(".bak"):
            continue
        backups.append(entry)
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return backups


def prune_old_backups(
    save_path: Path,
    *,
    keep: int,
    protect: Optional[Path] = None,
) -> list[Path]:
    """Delete backups beyond the newest ``keep``. Returns deleted paths.

    ``protect`` (if given) is never deleted even if it falls past the cutoff.
    """
    if keep < 0:
        return []
    backups = list_save_backups(save_path)
    deleted: list[Path] = []
    for old in backups[keep:]:
        if protect is not None and old.resolve() == protect.resolve():
            continue
        try:
            old.unlink()
            deleted.append(old)
        except OSError:
            pass
    return deleted


# ---------- source/item-array access ----------

SOURCE_INVENTORY = "inventory"
SOURCE_EQUIPMENT = "equipment"
SOURCE_SHELTER = "shelter"

DEFAULT_SAVE_DIR = Path.home() / "AppData" / "LocalLow" / "OrderOfMeta" / "Cargo Hunters"
DEFAULT_SAVE_PATH = DEFAULT_SAVE_DIR / "offline.save"
CONDITION_FULL_VALUE = 4.0

# Some consumable/repair items store remaining uses in
# DurabilityComponent_durability but do not serialize DurabilityComponent_md.
# These are known full-use counts seen/inferred from the game data/save values.
USE_COUNT_TEMPLATE_MAX = {
    # Fabric/armor/weapon/bodypart repair kits: 3 charges.
    "9d991ab8-3a58-4751-a758-d86279872dd1": 3.0,  # Fabrics repair kit
    "755fa97a-40c0-4e85-ade5-c58bc63db4dd": 3.0,  # Armor repair kit
    "0a92b724-46a9-45b9-9837-67f3b2aecf1d": 3.0,  # Weapon repair kit
    "76ad31a4-2fe1-4ca4-a2b6-a5133e776ff1": 3.0,  # Bodypart repair kit

    # Larger repair/medical-style kits store larger use pools.
    "2613c37e-2678-4627-a87d-37dc46274d8a": 100.0,  # Built-in Repair Kit
    "6d249ffa-bd3b-43c2-89fa-4992be7af2a9": 200.0,  # Mini Repair Kit
    "b26c003f-496e-41b3-bc6f-70beaa76ac0e": 500.0,  # Repair Kit
    "fd065383-2b84-41e8-80fd-041bf8d19ab6": 1600.0,  # MaRS

    # Tool consumables.
    "b1c818fa-0ae5-415c-b407-a9c1a92feb14": 3.0,  # Grinder disk
}

# StackableComponent_quantity maxima. Explicit values cover known special cases;
# remaining stackables can fall back to the largest observed stack in the save.
STACK_COUNT_TEMPLATE_MAX = {
    "cb567810-cc82-424f-893f-299c704ffb12": 10_000,  # Cash
    "fd72a971-80d2-4dd3-9d56-22dbbd066642": 5,  # Lockpick

    # Common ammo stacks.
    "98e1e51b-4f8b-4512-bd34-2a37a0eb2930": 60,  # .45ACP
    "394783c8-3fa6-4573-a154-fa52921eeb15": 60,  # .45ACP AP
    "4ec3fa7f-f8a9-4fce-bcdd-efda2dbf0826": 60,  # .45ACP E
    "0e9060f6-f0d4-4f62-9457-c9165a959b4d": 60,  # 57x28
    "cc5a5fde-6c82-45af-babf-3d6875a26911": 60,  # 57x28 E
    "e9fd9b62-e02b-435a-88fc-87dd5597a00a": 60,  # 57x28 AP
    "e3e576c5-7cf4-4e9b-8283-2fd5eb4676d2": 60,  # 9x19
    "bb5ca07d-ad87-45c0-96da-dd153a03bcf7": 60,  # 9x19 AP
    "d08d0179-5c5f-4ae4-bf1f-8032d52f3498": 60,  # 9x19 E
    "22d7f633-57d4-4906-b0b1-ea0299203826": 60,  # 9x19 C-II
    "3222f212-6e49-4391-8eff-a929474e1e4c": 60,  # 9x19 C-III
    "82700397-d829-4dbd-8e84-38120f0d0ba2": 60,  # 9x19 B-III
    "9f5c76b9-f09e-4bee-ada1-2ed0afe7ce17": 60,  # 9x19 A-II
    "bea27756-b4e4-4b61-b572-c8f81e3f7e8b": 60,  # 9x19 B-II
    "cb6d4579-12b9-478c-94e4-579f01a45a83": 60,  # 9x19 A-III

    # Authoritative StackCapacity from repositoriesgroup item_templates.
    "36c7a7d2-7eca-400f-a28e-68613070505c": 60,  # 5.45x39 E
    "a7721ae4-5bb3-4c09-8605-e18272b59ac6": 60,  # 5.45x39
    "deeb8cc6-ef24-4194-8139-ffe155d1b87f": 60,  # 5.45x39 AP
    "2da75073-af27-4924-a4c1-d27d2b834df7": 60,  # 5.45x39 B-III
    "32720498-5feb-4bee-9186-90dd08311206": 60,  # 5.45x39 B-II
    "657ea17a-0c61-4ef3-b993-639e0791ab2d": 60,  # 5.45x39 C-III
    "6ea5a413-f9d8-421a-8465-d3c8f8802c72": 60,  # 5.45x39 A-II
    "80c9682f-bf58-4176-a457-050e581c80a4": 60,  # 5.45x39 C-II
    "c06bca0a-f8f3-4af8-a9e1-26c15e62c443": 60,  # 5.45x39 A-III
}

def get_use_count_max(item: dict) -> Optional[float]:
    """Return known max uses for items whose durability field is a use count."""
    template_id = (item.get("TemplateId") or "").strip()
    return USE_COUNT_TEMPLATE_MAX.get(template_id)


def get_stack_count_max(item: dict, observed_maxes: Optional[dict[str, int]] = None) -> Optional[int]:
    """Return known or observed max stack size for an item."""
    template_id = (item.get("TemplateId") or "").strip()
    if template_id in STACK_COUNT_TEMPLATE_MAX:
        return STACK_COUNT_TEMPLATE_MAX[template_id]
    if observed_maxes is not None:
        return observed_maxes.get(template_id)
    return None


def get_stack_count_max_for_template(template_id: str) -> Optional[int]:
    """Return an explicit max stack size for a template, if one is known."""
    return STACK_COUNT_TEMPLATE_MAX.get((template_id or "").strip())


def get_items_list(data: dict, source: str) -> list[dict]:
    """Return a live reference to the items array for the given source."""
    if source == SOURCE_INVENTORY:
        return data["InventoryDto"]["ItemsContainerDto"]["Items"]
    if source == SOURCE_EQUIPMENT:
        return data["EquipmentDto"]["Items"]
    if source == SOURCE_SHELTER:
        return data["ShelterItemDto"]["Container"]["Items"]
    raise ValueError(f"unknown source: {source!r}")


def get_inventory_container(data: dict) -> dict:
    return data["InventoryDto"]["ItemsContainerDto"]


def get_backpack_id(data: dict) -> str:
    container = get_inventory_container(data)
    items: list[dict] = container["Items"]
    owner_id: str = container["OwnerItemId"]
    backpack_candidates = [it for it in items if it.get("ParentId") == owner_id]
    if len(backpack_candidates) != 1:
        raise ValueError(
            f"Expected exactly one backpack-style container (ParentId == {owner_id}); "
            f"found {len(backpack_candidates)}."
        )
    return backpack_candidates[0]["Id"]


def list_backpack_items(data: dict) -> list[dict]:
    """Return items whose ``ParentId`` is the backpack (i.e. top-level loose inventory)."""
    backpack_id = get_backpack_id(data)
    container = get_inventory_container(data)
    return [it for it in container["Items"] if it.get("ParentId") == backpack_id]


def remove_items_by_ids(data: dict, ids: set[str]) -> int:
    """Remove items by Id from every source. Also recursively removes any items
    parented to those (so removing a container removes its contents).
    Returns the total number of items removed."""
    total = 0
    for source in (SOURCE_INVENTORY, SOURCE_EQUIPMENT, SOURCE_SHELTER):
        try:
            items = get_items_list(data, source)
        except KeyError:
            continue
        to_remove: set[str] = set(ids)
        changed = True
        while changed:
            changed = False
            for it in items:
                if it.get("ParentId") in to_remove and it["Id"] not in to_remove:
                    to_remove.add(it["Id"])
                    changed = True
        before = len(items)
        kept = [it for it in items if it["Id"] not in to_remove]
        items[:] = kept
        total += before - len(items)
    return total


def split_stack_item(
    data: dict,
    *,
    source: str,
    item_id: str,
    split_quantity: int,
    dims: dict[str, tuple[int, int]],
    grid_width: Optional[int] = None,
) -> dict[str, object]:
    """Split one stackable item into two stacks in the same container.

    ``split_quantity`` is moved into a cloned item with a new Id. The original
    stack keeps ``current_quantity - split_quantity``. The new item is placed in
    the first free slot in the same parent container using the same occupancy
    logic as item insertion.
    """
    items = get_items_list(data, source)
    item = next((it for it in items if it.get("Id") == item_id), None)
    if item is None:
        raise ValueError(f"Could not find item Id {item_id!r} in {source}.")

    ad = ((item.get("AdditionalData") or {}).get("_data") or {})
    if "StackableComponent_quantity" not in ad:
        raise ValueError("Selected item does not store a stack quantity and cannot be split.")
    try:
        current_quantity = int(ad["StackableComponent_quantity"])
    except (TypeError, ValueError):
        raise ValueError(f"Selected item has an invalid stack quantity: {ad.get('StackableComponent_quantity')!r}") from None
    if current_quantity < 2:
        raise ValueError("Selected stack must have at least 2 items to split.")
    if split_quantity < 1 or split_quantity >= current_quantity:
        raise ValueError(f"Split quantity must be between 1 and {current_quantity - 1}.")

    parent_id = item.get("ParentId")
    if not parent_id:
        raise ValueError("Selected item has no ParentId, so the split stack cannot be placed.")

    template_id = item.get("TemplateId", "")
    w, h = dims.get(template_id, (1, 1))
    effective_grid_width = grid_width or 10
    occ = compute_occupancy(items, parent_id, dims)
    pos_i, pos_j = find_free_slot(occ, w, h, grid_width=effective_grid_width)

    new_item = copy.deepcopy(item)
    new_item["Id"] = str(uuid.uuid4())
    new_item["ParentId"] = parent_id
    new_item["Position"] = {"I": pos_i, "J": pos_j}
    new_ad = new_item.setdefault("AdditionalData", {}).setdefault("_data", {})
    new_ad["StackableComponent_quantity"] = int(split_quantity)

    ad["StackableComponent_quantity"] = int(current_quantity - split_quantity)
    items.append(new_item)

    return {
        "source": source,
        "template_id": template_id,
        "original_id": item_id,
        "new_id": new_item["Id"],
        "original_quantity": current_quantity - split_quantity,
        "new_quantity": split_quantity,
        "position": (pos_i, pos_j),
        "item_size": (w, h),
        "grid_width": effective_grid_width,
    }


def _build_observed_stack_max(data: dict) -> dict[str, int]:
    observed: dict[str, int] = {}
    for source in (SOURCE_INVENTORY, SOURCE_EQUIPMENT, SOURCE_SHELTER):
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


def set_items_condition_durability_full(
    data: dict,
    ids: set[str],
    *,
    top_off_stacks: bool = True,
    stack_max_by_template: Optional[Mapping[str, int]] = None,
) -> dict[str, int]:
    """Set selected items to full condition/durability where those stats exist.

    Condition uses ``Condition_d`` as current and ``Condition_mt`` as a serialized
    condition cap. Body parts can have ``Condition_d == Condition_mt`` while both
    values are below the true full value and the part still has reduced function,
    so "100%" condition means setting both fields to ``CONDITION_FULL_VALUE``.

    Durability uses ``DurabilityComponent_durability`` as current and
    ``DurabilityComponent_md`` as max. If no max durability is present, known
    repair/medical/tool use-count items are refilled from ``USE_COUNT_TEMPLATE_MAX``;
    unknown no-max entries are left unchanged rather than guessed.

    If ``top_off_stacks`` is enabled, stackable item quantities are restored to a
    known template max or, for unknown stackables, the highest observed quantity
    for that same template in the loaded save.
    """
    stats = {
        "matched": 0,
        "changed": 0,
        "condition": 0,
        "durability": 0,
        "uses": 0,
        "stacks": 0,
        "skipped_no_stats": 0,
        "skipped_durability_no_max": 0,
        "skipped_uses_unknown_max": 0,
        "skipped_stack_unknown_max": 0,
    }

    observed_stack_max = _build_observed_stack_max(data) if top_off_stacks else {}

    for source in (SOURCE_INVENTORY, SOURCE_EQUIPMENT, SOURCE_SHELTER):
        try:
            items = get_items_list(data, source)
        except KeyError:
            continue
        for item in items:
            if item.get("Id") not in ids:
                continue

            stats["matched"] += 1
            additional = item.setdefault("AdditionalData", {})
            ad = additional.setdefault("_data", {})
            changed = False
            had_stat = False

            if "Condition_d" in ad or "Condition_mt" in ad:
                had_stat = True
                target = CONDITION_FULL_VALUE
                if ad.get("Condition_mt") != target:
                    ad["Condition_mt"] = target
                    changed = True
                if ad.get("Condition_d") != target:
                    ad["Condition_d"] = target
                    changed = True
                stats["condition"] += 1

            has_durability_fields = (
                "DurabilityComponent_durability" in ad
                or "DurabilityComponent_md" in ad
            )
            # Brand-new full-charge items (e.g. fresh MaRS, repair kits) omit
            # the durability field entirely; the game treats missing == full.
            # If we know this template's use-count cap, materialize the field
            # at full so it stops being invisible and stops counting as
            # "skipped_no_stats" on subsequent repair passes.
            inferred_uses_target = None
            if not has_durability_fields and "Condition_d" not in ad and "Condition_mt" not in ad:
                inferred_uses_target = get_use_count_max(item)
            if has_durability_fields or inferred_uses_target is not None:
                had_stat = True
                target = ad.get("DurabilityComponent_md")
                if target is None:
                    uses_target = inferred_uses_target if inferred_uses_target is not None else get_use_count_max(item)
                    if uses_target is None:
                        stats["skipped_durability_no_max"] += 1
                        stats["skipped_uses_unknown_max"] += 1
                    elif ad.get("DurabilityComponent_durability") != uses_target:
                        ad["DurabilityComponent_durability"] = uses_target
                        changed = True
                        stats["uses"] += 1
                    else:
                        stats["uses"] += 1
                elif ad.get("DurabilityComponent_durability") != target:
                    ad["DurabilityComponent_durability"] = target
                    changed = True
                    stats["durability"] += 1
                else:
                    stats["durability"] += 1

            if top_off_stacks:
                template_id = (item.get("TemplateId") or "").strip()
                stack_target = None
                if stack_max_by_template is not None:
                    stack_target = stack_max_by_template.get(template_id)
                has_stack_quantity = "StackableComponent_quantity" in ad
                if stack_target is None and has_stack_quantity:
                    stack_target = get_stack_count_max(item, observed_stack_max)
                if stack_target is not None or has_stack_quantity:
                    had_stat = True
                    if stack_target is None:
                        stats["skipped_stack_unknown_max"] += 1
                    else:
                        try:
                            current_quantity = int(ad.get("StackableComponent_quantity", 1))
                        except (TypeError, ValueError):
                            current_quantity = 1
                        # Top off only; do not reduce intentionally-overfull stacks.
                        if current_quantity < stack_target:
                            ad["StackableComponent_quantity"] = stack_target
                            changed = True
                            stats["stacks"] += 1

            if not had_stat:
                stats["skipped_no_stats"] += 1
            if changed:
                stats["changed"] += 1

    return stats


# ---------- catalog (template_id -> friendly name / dimensions) ----------

BUILTIN_TEMPLATE_NAMES = {
    # Present in saves/pricelists but missing from all_items_detailed.csv.
    "cb567810-cc82-424f-893f-299c704ffb12": "Cash",
    "fd72a971-80d2-4dd3-9d56-22dbbd066642": "Lockpick",
}

BUILTIN_CATALOG_ROWS = [
    {
        "ItemName": "Cash",
        "ItemID": "cb567810-cc82-424f-893f-299c704ffb12",
        "BasePrice": "1",
        "SellPrice": "1",
        "PriceCoefficient": "1.00",
        "Weight": "0",
        "Width": "1",
        "Height": "1",
        "InventorySize": "1",
        "CategoryID": "currency",
        "SubcategoryID": "cash",
        "ShopName": "Built-in",
        "PriceSource": "Built-in",
    },
    {
        "ItemName": "Lockpick",
        "ItemID": "fd72a971-80d2-4dd3-9d56-22dbbd066642",
        "BasePrice": "0",
        "SellPrice": "0",
        "PriceCoefficient": "1.00",
        "Weight": "0.01",
        "Width": "1",
        "Height": "1",
        "InventorySize": "1",
        "CategoryID": "key",
        "SubcategoryID": "lockpick",
        "ShopName": "Built-in",
        "PriceSource": "Built-in",
    },
]

def load_template_names(csv_path: Path) -> dict[str, str]:
    out: dict[str, str] = dict(BUILTIN_TEMPLATE_NAMES)
    if not csv_path.exists():
        return out
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            tid = (row.get("ItemID") or "").strip()
            name = (row.get("ItemName") or "").strip()
            if tid and name and tid not in out:
                out[tid] = name
    return out


def load_template_dims(csv_path: Path) -> dict[str, tuple[int, int]]:
    """Return ``{template_id: (width, height)}`` from the catalog CSV.

    Used for occupancy-aware placement. Items missing from the CSV (or with
    unparseable sizes) default to 1x1.
    """
    if not csv_path.exists():
        return {}
    out: dict[str, tuple[int, int]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            tid = (row.get("ItemID") or "").strip()
            if not tid or tid in out:
                continue
            try:
                w = max(1, int(row.get("Width") or 1))
                h = max(1, int(row.get("Height") or 1))
            except ValueError:
                w, h = 1, 1
            out[tid] = (w, h)
    return out


def _item_size(template_id: str, dims: dict[str, tuple[int, int]]) -> tuple[int, int]:
    return dims.get(template_id, (1, 1))


def _container_grid_from_item(item: dict) -> tuple[Optional[int], Optional[int]]:
    """Read BaseComponent_width/_height from an item's AdditionalData, if any."""
    ad = ((item.get("AdditionalData") or {}).get("_data") or {})
    w = ad.get("BaseComponent_width")
    h = ad.get("BaseComponent_height")
    try:
        w = int(w) if w is not None else None
        h = int(h) if h is not None else None
    except (TypeError, ValueError):
        w = h = None
    return w, h


# ---------- container discovery ----------

@dataclass(frozen=True)
class Container:
    label: str
    source: str
    owner_item_id: str
    grid_width: Optional[int]   # from BaseComponent_width if known
    grid_height: Optional[int]
    template_id: Optional[str]  # template of the container item, if any


def _children_of(items: list[dict], owner_id: str) -> list[dict]:
    return [it for it in items if it.get("ParentId") == owner_id]


def discover_containers(
    data: dict,
    names: Optional[dict[str, str]] = None,
) -> list[Container]:
    """Find every place a new item could be placed.

    Returns the backpack, the shelter root, and every equipment / sub-container
    that already has children (so we don't surface single-slot items like
    weapons-without-attachments unless they actually act as containers).
    """
    names = names or {}
    out: list[Container] = []

    # --- Backpack ---
    try:
        inv_container = get_inventory_container(data)
        inv_items = inv_container["Items"]
        bp_id = get_backpack_id(data)
        bp_item = next((it for it in inv_items if it["Id"] == bp_id), None)
        gw, gh = _container_grid_from_item(bp_item) if bp_item else (None, None)
        out.append(Container(
            label="Backpack",
            source=SOURCE_INVENTORY,
            owner_item_id=bp_id,
            grid_width=gw,
            grid_height=gh,
            template_id=bp_item["TemplateId"] if bp_item else None,
        ))
    except (KeyError, ValueError):
        pass

    # --- Shelter ---
    try:
        shelter_owner = data["ShelterItemDto"]["Container"]["OwnerItemId"]
        shelter_item = data["ShelterItemDto"].get("Item") or {}
        gw, gh = _container_grid_from_item(shelter_item)
        out.append(Container(
            label="Shelter",
            source=SOURCE_SHELTER,
            owner_item_id=shelter_owner,
            grid_width=gw,
            grid_height=gh,
            template_id=shelter_item.get("TemplateId"),
        ))
    except KeyError:
        pass

    # --- Equipment & nested containers in each source ---
    for source in (SOURCE_INVENTORY, SOURCE_EQUIPMENT, SOURCE_SHELTER):
        try:
            items = get_items_list(data, source)
        except KeyError:
            continue
        # Build child index for speed.
        child_count: dict[str, int] = {}
        for it in items:
            pid = it.get("ParentId")
            if pid:
                child_count[pid] = child_count.get(pid, 0) + 1
        for it in items:
            iid = it["Id"]
            if child_count.get(iid, 0) == 0:
                continue
            # Skip duplicates already added (backpack, shelter root).
            if any(c.source == source and c.owner_item_id == iid for c in out):
                continue
            gw, gh = _container_grid_from_item(it)
            tid = it.get("TemplateId", "")
            label = names.get(tid) or tid[:8] or "container"
            # Prefix to make scope obvious in the dropdown.
            prefix = {
                SOURCE_INVENTORY: "Inv",
                SOURCE_EQUIPMENT: "Equip",
                SOURCE_SHELTER: "Shelter",
            }[source]
            out.append(Container(
                label=f"{prefix}: {label}",
                source=source,
                owner_item_id=iid,
                grid_width=gw,
                grid_height=gh,
                template_id=tid,
            ))
    return out


# ---------- occupancy & free-slot search ----------

def compute_occupancy(
    items: list[dict],
    owner_id: str,
    dims: dict[str, tuple[int, int]],
) -> set[tuple[int, int]]:
    """Return cells occupied by items directly parented to ``owner_id``."""
    occ: set[tuple[int, int]] = set()
    for it in items:
        if it.get("ParentId") != owner_id:
            continue
        pos = it.get("Position") or {}
        i = int(pos.get("I", 0))
        j = int(pos.get("J", 0))
        if i < 0 or j < 0:  # equipped/special slot, not on a grid
            continue
        w, h = _item_size(it["TemplateId"], dims)
        for di in range(w):
            for dj in range(h):
                occ.add((i + di, j + dj))
    return occ


def compute_backpack_occupancy(
    data: dict,
    dims: dict[str, tuple[int, int]],
) -> set[tuple[int, int]]:
    """Back-compat wrapper around :func:`compute_occupancy` for the backpack."""
    return compute_occupancy(
        get_items_list(data, SOURCE_INVENTORY),
        get_backpack_id(data),
        dims,
    )


def find_free_slot(
    occ: set[tuple[int, int]],
    w: int,
    h: int,
    *,
    grid_width: int = 10,
    max_rows: int = 256,
) -> tuple[int, int]:
    """Find the first free top-left (I,J) for a WxH item. Row-major scan
    (J outer, I inner). Mutates nothing.
    """
    if w > grid_width:
        raise ValueError(f"item width {w} exceeds grid_width {grid_width}")
    for j in range(max_rows):
        for i in range(grid_width - w + 1):
            if all(
                (i + di, j + dj) not in occ
                for di in range(w)
                for dj in range(h)
            ):
                return i, j
    raise RuntimeError(
        f"No free {w}x{h} slot found within {max_rows} rows at grid_width={grid_width}"
    )


# ---------- move items between containers ----------

def _all_items_index(
    data: dict,
) -> tuple[dict[str, tuple[str, dict]], dict[str, list[str]]]:
    """Return (by_id, children_map) across all three item arrays.

    by_id[item_id] = (source_name, item_dict)
    children_map[parent_id] = [child_item_id, ...]
    """
    by_id: dict[str, tuple[str, dict]] = {}
    children: dict[str, list[str]] = {}
    for src in (SOURCE_INVENTORY, SOURCE_EQUIPMENT, SOURCE_SHELTER):
        try:
            items = get_items_list(data, src)
        except KeyError:
            continue
        for it in items:
            iid = it.get("Id")
            if not iid:
                continue
            by_id[iid] = (src, it)
            pid = it.get("ParentId") or ""
            if pid:
                children.setdefault(pid, []).append(iid)
    return by_id, children


def _collect_descendants(
    item_id: str, children_map: Mapping[str, list[str]]
) -> list[str]:
    """Return [item_id, ...descendants] in BFS order."""
    out: list[str] = [item_id]
    queue: list[str] = list(children_map.get(item_id, []))
    while queue:
        cur = queue.pop(0)
        out.append(cur)
        queue.extend(children_map.get(cur, []))
    return out


def move_items_to_container(
    data: dict,
    item_ids: Iterable[str],
    *,
    dest_source: str,
    dest_owner_id: str,
    dest_grid_width: Optional[int],
    dest_grid_height: Optional[int],
    dims: dict[str, tuple[int, int]],
    names: Optional[Mapping[str, str]] = None,
) -> dict[str, object]:
    """Move the given items (and everything parented under them) into the
    destination container's grid.

    Validates space first; raises ``ValueError`` with a human-readable message
    if even one item cannot fit. Children of moved items travel with the
    parent — they keep their ``ParentId`` chain intact and are appended to the
    destination ``source`` array along with their anchor.

    Same-container moves are allowed (the moved items are excluded from the
    destination's occupancy so they can be re-laid-out cleanly).
    """
    requested = [iid for iid in item_ids if iid]
    if not requested:
        raise ValueError("No items selected to move.")

    by_id, children_map = _all_items_index(data)
    name_lookup = dict(names or {})

    def _display_name(item_id: str) -> str:
        entry = by_id.get(item_id)
        if not entry:
            return item_id[:8]
        return name_lookup.get(entry[1].get("TemplateId", ""), item_id[:8])

    # Validate every requested id exists.
    missing = [iid for iid in requested if iid not in by_id]
    if missing:
        raise ValueError(
            "Some selected items no longer exist in the save: "
            + ", ".join(iid[:8] for iid in missing)
        )

    # Deduplicate anchors: if a selected item is a descendant of another
    # selected item, drop it (it'll move with its ancestor).
    requested_set = set(requested)
    anchors: list[str] = []
    seen: set[str] = set()
    for iid in requested:
        if iid in seen:
            continue
        seen.add(iid)
        parent = by_id[iid][1].get("ParentId") or ""
        is_descendant = False
        while parent:
            if parent in requested_set:
                is_descendant = True
                break
            parent_entry = by_id.get(parent)
            if not parent_entry:
                break
            parent = parent_entry[1].get("ParentId") or ""
        if not is_descendant:
            anchors.append(iid)

    if not anchors:
        raise ValueError("No top-level items to move (everything selected was a descendant).")

    # The destination cannot be one of the anchors or live inside any anchor's
    # subtree (would create a parent-cycle).
    full_move_set: set[str] = set()
    for iid in anchors:
        full_move_set.update(_collect_descendants(iid, children_map))
    if dest_owner_id in full_move_set:
        raise ValueError(
            f"Cannot move {_display_name(dest_owner_id)!r} into itself or one of its own contents."
        )

    # No-op detection: every anchor already lives in dest with valid position.
    all_already_in_dest = all(
        by_id[iid][1].get("ParentId") == dest_owner_id
        and by_id[iid][0] == dest_source
        for iid in anchors
    )
    if all_already_in_dest:
        raise ValueError(
            "Every selected item is already in the destination container — nothing to move."
        )

    grid_w = max(1, int(dest_grid_width or 10))
    grid_h = int(dest_grid_height) if dest_grid_height else None

    # Build destination occupancy from items currently parented to dest, but
    # exclude items that are themselves being moved (they get a fresh layout).
    dest_arr = get_items_list(data, dest_source)
    occ: set[tuple[int, int]] = set()
    for it in dest_arr:
        if it.get("ParentId") != dest_owner_id:
            continue
        if it.get("Id") in full_move_set:
            continue
        pos = it.get("Position") or {}
        try:
            i = int(pos.get("I", 0))
            j = int(pos.get("J", 0))
        except (TypeError, ValueError):
            continue
        if i < 0 or j < 0:
            continue
        w, h = dims.get(it.get("TemplateId", ""), (1, 1))
        for di in range(w):
            for dj in range(h):
                occ.add((i + di, j + dj))

    sizes: dict[str, tuple[int, int]] = {}
    for iid in anchors:
        tid = by_id[iid][1].get("TemplateId", "")
        sizes[iid] = dims.get(tid, (1, 1))

    # Pack largest-first (rectangle area descending, then by width).
    sorted_anchors = sorted(
        anchors, key=lambda i: (-(sizes[i][0] * sizes[i][1]), -sizes[i][0])
    )

    placements: list[tuple[str, int, int]] = []
    failures: list[tuple[str, int, int]] = []
    max_rows = grid_h if grid_h else 256
    for iid in sorted_anchors:
        w, h = sizes[iid]
        if w > grid_w or (grid_h is not None and h > grid_h):
            failures.append((iid, w, h))
            continue
        try:
            i, j = find_free_slot(occ, w, h, grid_width=grid_w, max_rows=max_rows)
        except (RuntimeError, ValueError):
            failures.append((iid, w, h))
            continue
        # Hard cap: if dest has a known height, enforce it.
        if grid_h is not None and j + h > grid_h:
            failures.append((iid, w, h))
            continue
        placements.append((iid, i, j))
        for di in range(w):
            for dj in range(h):
                occ.add((i + di, j + dj))

    if failures:
        lines = [
            f"  - {_display_name(iid)} ({w}x{h})"
            for iid, w, h in failures
        ]
        capacity = grid_w * grid_h if grid_h else None
        used = len(occ)
        free_txt = (
            f"  About {capacity - used} of {capacity} cells free."
            if capacity is not None
            else f"  Used cells before placement: {used}."
        )
        raise ValueError(
            "Not enough room in the destination container for these item(s):\n"
            + "\n".join(lines)
            + f"\nDestination grid: {grid_w}x{grid_h if grid_h else '?'}.\n"
            + free_txt
        )

    placement_map = {iid: (i, j) for iid, i, j in placements}

    # Remove every moving item from its current source array.
    moves_by_source: dict[str, set[str]] = {}
    for iid in full_move_set:
        src, _it = by_id[iid]
        moves_by_source.setdefault(src, set()).add(iid)
    for src, ids_to_remove in moves_by_source.items():
        arr = get_items_list(data, src)
        arr[:] = [it for it in arr if it.get("Id") not in ids_to_remove]

    # Update anchor ParentId/Position; descendants keep their chain.
    for iid, (i, j) in placement_map.items():
        item = by_id[iid][1]
        item["ParentId"] = dest_owner_id
        item["Position"] = {"I": i, "J": j}

    # Append anchors and their descendants (anchor first) to dest array.
    ordered: list[dict] = []
    for anchor_id in anchors:
        for desc_id in _collect_descendants(anchor_id, children_map):
            ordered.append(by_id[desc_id][1])
    dest_arr.extend(ordered)

    return {
        "moved_anchor_count": len(anchors),
        "moved_total_count": len(full_move_set),
        "placements": [
            {
                "id": iid,
                "name": _display_name(iid),
                "i": i,
                "j": j,
                "size": sizes[iid],
            }
            for iid, i, j in placements
        ],
        "dest_source": dest_source,
        "dest_owner_id": dest_owner_id,
        "dest_grid_width": grid_w,
        "dest_grid_height": grid_h,
    }


# ---------- account ----------

def get_experience(data: dict) -> dict:
    return data["AccountDto"]["ExperienceDto"]


def get_skills(data: dict) -> dict:
    return data["AccountDto"]["SkillsDto"]


def set_experience(data: dict, *, level: Optional[int], xp: Optional[int],
                   next_goal: Optional[int]) -> None:
    exp = get_experience(data)
    if level is not None:
        exp["Level"] = int(level)
    if xp is not None:
        exp["ExperiencePoints"] = int(xp)
    if next_goal is not None:
        exp["NextLevelExperienceGoal"] = int(next_goal)


def set_skill_points(data: dict, count: int) -> None:
    get_skills(data)["SkillPointsCount"] = int(count)


def set_skill_levels(
    data: dict,
    changes: Mapping[int, Mapping[str, Optional[int]]],
) -> dict:
    """Apply per-skill ``Level`` / ``NextLevelExperienceGoal`` updates in place.

    ``changes`` maps a skill ``Id`` to a dict with optional ``"Level"`` and
    ``"NextLevelExperienceGoal"`` keys (``None`` values are ignored). Returns a
    summary ``{"updated": int, "missing": [skill_id, ...]}`` so the caller can
    log/report what landed and what wasn't found in the save.
    """
    skills_list = get_skills(data).get("Skills") or []
    updated = 0
    seen: set[int] = set()
    for entry in skills_list:
        try:
            sid = int(entry.get("Id"))
        except (TypeError, ValueError):
            continue
        if sid not in changes:
            continue
        seen.add(sid)
        upd = changes[sid] or {}
        if upd.get("Level") is not None:
            entry["Level"] = int(upd["Level"])
        if upd.get("NextLevelExperienceGoal") is not None:
            entry["NextLevelExperienceGoal"] = int(upd["NextLevelExperienceGoal"])
        updated += 1
    missing = sorted(int(k) for k in changes.keys() if int(k) not in seen)
    return {"updated": updated, "missing": missing}


def set_nickname(data: dict, nickname: str) -> None:
    data["AccountDto"]["Nickname"] = nickname
