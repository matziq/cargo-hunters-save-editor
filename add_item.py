"""Insert one or more items into the player's inventory in a Cargo Hunters offline.save.

The InventoryDto contains a sub-container (the backpack). New items are appended to
InventoryDto.ItemsContainerDto.Items with ParentId set to the backpack's Id and
placed at I=0, J=(max_used_J + n) so they never collide with an existing item.

A timestamped backup is written next to the save before the file is rewritten.

Optional AdditionalData supported:
  --qty N          -> StackableComponent_quantity = N (stackable resources / ammo)
  --condition X    -> Condition_d = Condition_mt = X (pristine "Condition" gear)
  --durability X   -> DurabilityComponent_durability = DurabilityComponent_md = X
  --count N        -> create N separate item instances
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Optional

from save_io import (
    DEFAULT_SAVE_PATH,
    SOURCE_INVENTORY,
    compute_occupancy,
    discover_containers,
    find_free_slot,
    get_backpack_id,
    get_items_list,
    load_save,
    load_template_dims,
    load_template_names,
    write_save,
)

DEFAULT_CSV = Path(__file__).resolve().parent / "all_items_detailed.csv"


def _build_new_item(
    *,
    parent_id: str,
    template_id: str,
    pos_i: int,
    pos_j: int,
    qty: Optional[int],
    condition: Optional[float],
    durability: Optional[float],
) -> dict:
    item: dict = {
        "Id": str(uuid.uuid4()),
        "ParentId": parent_id,
        "TemplateId": template_id,
        "Position": {"I": pos_i, "J": pos_j},
        "IsInspected": True,
    }
    extra: dict = {}
    if qty is not None:
        extra["StackableComponent_quantity"] = int(qty)
    if condition is not None:
        extra["Condition_d"] = float(condition)
        extra["Condition_mt"] = float(condition)
    if durability is not None:
        extra["DurabilityComponent_durability"] = float(durability)
        extra["DurabilityComponent_md"] = float(durability)
    if extra:
        item["AdditionalData"] = {"_data": extra}
    return item


def add_items(
    save_path: Path,
    template_id: str,
    item_name: str,
    *,
    qty: Optional[int] = None,
    count: int = 1,
    condition: Optional[float] = None,
    durability: Optional[float] = None,
    grid_width: Optional[int] = None,
    csv_path: Optional[Path] = None,
    source: str = SOURCE_INVENTORY,
    owner_id: Optional[str] = None,
) -> None:
    """Insert ``count`` copies of ``template_id`` into a container.

    ``source`` + ``owner_id`` selects the destination container. If
    ``owner_id`` is None and ``source == SOURCE_INVENTORY``, the player's
    backpack is used.

    ``grid_width`` overrides the container's declared
    ``BaseComponent_width``; if neither is known we default to 10.
    """
    data = load_save(save_path)

    if owner_id is None:
        if source != SOURCE_INVENTORY:
            raise SystemExit(f"owner_id is required for source={source!r}")
        owner_id = get_backpack_id(data)

    items = get_items_list(data, source)

    dims = load_template_dims(csv_path or DEFAULT_CSV)

    # Pick grid width: explicit override > container's BaseComponent_width > 10.
    effective_grid_width = grid_width
    if effective_grid_width is None:
        names = load_template_names(csv_path or DEFAULT_CSV)
        containers = discover_containers(data, names)
        match = next(
            (c for c in containers if c.source == source and c.owner_item_id == owner_id),
            None,
        )
        if match and match.grid_width:
            effective_grid_width = match.grid_width
        else:
            effective_grid_width = 10

    occ = compute_occupancy(items, owner_id, dims)
    w, h = dims.get(template_id, (1, 1))

    new_items: list[dict] = []
    for _ in range(count):
        pos_i, pos_j = find_free_slot(occ, w, h, grid_width=effective_grid_width)
        for di in range(w):
            for dj in range(h):
                occ.add((pos_i + di, pos_j + dj))
        new_items.append(
            _build_new_item(
                parent_id=owner_id,
                template_id=template_id,
                pos_i=pos_i,
                pos_j=pos_j,
                qty=qty,
                condition=condition,
                durability=durability,
            )
        )

    items.extend(new_items)
    backup = write_save(save_path, data)

    if backup is not None:
        print(f"Backup: {backup.name}")
    print(
        f"  dest=({source}:{owner_id})  grid_width={effective_grid_width}  "
        f"item_size={w}x{h}"
    )
    for it in new_items:
        extras: list[str] = []
        ad = it.get("AdditionalData", {}).get("_data", {})
        if "StackableComponent_quantity" in ad:
            extras.append(f"qty={ad['StackableComponent_quantity']}")
        if "Condition_d" in ad:
            extras.append(f"cond={ad['Condition_d']}")
        if "DurabilityComponent_durability" in ad:
            extras.append(f"dur={ad['DurabilityComponent_durability']}")
        suffix = (" " + " ".join(extras)) if extras else ""
        pos = it["Position"]
        print(
            f"  + {item_name:<24} Template={template_id}  "
            f"Id={it['Id']}  pos=({pos['I']},{pos['J']}){suffix}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Insert one or more items into a Cargo Hunters offline.save.")
    parser.add_argument("--save", default=str(DEFAULT_SAVE_PATH))
    parser.add_argument("--template", required=True, help="TemplateId GUID of the item")
    parser.add_argument("--name", default="<unnamed>", help="Friendly name (for log output only)")
    parser.add_argument("--qty", type=int, default=None,
                        help="StackableComponent_quantity for stackables (e.g. resources, ammo).")
    parser.add_argument("--count", type=int, default=1,
                        help="Number of separate item instances to create (default 1).")
    parser.add_argument("--condition", type=float, default=None,
                        help="Set Condition_d == Condition_mt to this value (pristine).")
    parser.add_argument("--durability", type=float, default=None,
                        help="Set DurabilityComponent_durability == _md to this value (pristine).")
    parser.add_argument("--grid-width", type=int, default=None,
                        help="Override grid width (cells). Default: container's declared width, else 10.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help="Path to all_items_detailed.csv (for item width/height lookup).")
    parser.add_argument("--source", default=SOURCE_INVENTORY,
                        choices=["inventory", "equipment", "shelter"],
                        help="Items array to place into. Default: inventory (backpack).")
    parser.add_argument("--owner-id", default=None,
                        help="Container item Id to place into. Default: backpack (inventory only).")
    args = parser.parse_args()

    add_items(
        Path(args.save),
        args.template,
        args.name,
        qty=args.qty,
        count=args.count,
        condition=args.condition,
        durability=args.durability,
        grid_width=args.grid_width,
        csv_path=args.csv,
        source=args.source,
        owner_id=args.owner_id,
    )
