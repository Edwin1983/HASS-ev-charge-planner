from pathlib import Path

path = Path(__file__).resolve().parents[1] / "custom_components/ev_planner/core/planner.py"
lines = path.read_text(encoding="utf-8").splitlines()

price_index = None
for index, line in enumerate(lines):
    if "float(hour.price) > max_price + TOLERANCE" in line:
        price_index = index
        break

if price_index is None:
    raise SystemExit("maximum-price validation line not found")

if not any("self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY" in line for line in lines[price_index:price_index + 10]):
    close_index = None
    for index in range(price_index, min(price_index + 10, len(lines))):
        if lines[index].strip() == "):":
            close_index = index
            break
    if close_index is None:
        raise SystemExit("maximum-price validation closing line not found")
    indent = lines[price_index][:len(lines[price_index]) - len(lines[price_index].lstrip())]
    lines[close_index:close_index] = [
        indent + "and not (",
        indent + "    self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY",
        indent + "    and self.settings.pv_rounding == PV_ROUNDING_UP",
        indent + ")",
    ]

path.write_text("\n".join(lines) + "\n", encoding="utf-8")
