from pathlib import Path

path = Path(__file__).resolve().parents[1] / "custom_components/ev_planner/core/planner.py"
text = path.read_text(encoding="utf-8")
needle = """            if (\n                float(hour.price) > max_price + TOLERANCE\n                and float(decision.paid_energy_kwh) > TOLERANCE\n            ):"""
replacement = """            if (\n                float(hour.price) > max_price + TOLERANCE\n                and float(decision.paid_energy_kwh) > TOLERANCE\n                and not (\n                    self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY\n                    and self.settings.pv_rounding == PV_ROUNDING_UP\n                )\n            ):"""
if needle not in text:
    raise SystemExit("maximum-price validation block not found")
text = text.replace(needle, replacement, 1)
path.write_text(text, encoding="utf-8")
