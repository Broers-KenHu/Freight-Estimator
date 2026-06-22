from __future__ import annotations

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D


class MockApiCalculator(BaseFreightCalculator):
    provider_name = "Mock API"

    def quote(self, context):
        if self.channel.config_json.get("force_timeout"):
            return CalculatorResult.not_available(self.provider_name, "api_error", {"mock": "forced_timeout"})
        base = D(self.channel.config_json.get("base_amount", "19.95"))
        gst_rate = D(self.channel.config_json.get("gst_rate", "0.10"))
        fuel = D(self.channel.config_json.get("fuel_amount", "0"))
        ex_gst = base + fuel
        gst = ex_gst * gst_rate
        return CalculatorResult.available(
            self.provider_name,
            base_amount=base,
            fuel_amount=fuel,
            total_ex_gst=ex_gst,
            gst_amount=gst,
            total_inc_gst=ex_gst + gst,
            charge_lines=[
                ChargeLine("BASE", "Mock API base quote", base),
                ChargeLine("FUEL", "Mock API fuel", fuel),
            ],
            debug_breakdown={"mock": True, "destination": context.destination.__dict__},
        )
