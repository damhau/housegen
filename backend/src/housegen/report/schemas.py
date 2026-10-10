"""What the owner sets for the surfaces and volumes report (SIA 416, #47, #48).

The figures come from the scene (kit/quantities.js, computed in the viewer's scene page). The report
also needs what a model cannot say: prices, the weights of the living area, which flat owns a terrace or
a garden, the slab under the basement when the model has none, the names of the buildings.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Weights(BaseModel):
    """Shares of the weighted living area (page 9): practice, not a norm."""

    balcony: float = Field(0.5, ge=0, le=1)
    terrace: float = Field(0.33, ge=0, le=1)
    garden: float = Field(0.10, ge=0, le=1)


class Prices(BaseModel):
    """Unit prices of the estimate (page 8), CHF; null until entered."""

    building_m3: dict[str, float | None] = Field(
        default_factory=dict, description="CHF/m³ of SIA 416 volume, by building key (b0, b1…)"
    )
    land_m2: float | None = Field(None, ge=0)
    excavation_m3: float | None = Field(None, ge=0)
    pool_each: float | None = Field(None, ge=0)
    paving_m2: float | None = Field(None, ge=0)
    hedge_m: float | None = Field(None, ge=0)
    fence_m: float | None = Field(None, ge=0)
    tree_each: float | None = Field(None, ge=0)
    secondary_pct: float | None = Field(None, ge=0, le=100, description="CFC 5, % of CFC 2")
    sale_m2: dict[str, float | None] = Field(
        default_factory=dict, description="CHF per weighted m², by flat (App. 1…)"
    )


class ReportSettings(BaseModel):
    """The report's settings, saved on the project."""

    description: str | None = Field(
        None, max_length=200, description="e.g. villa de deux appartements"
    )
    parcel: str | None = Field(
        None, max_length=200, description="e.g. BF n° 3013, Le Mont-sur-Lausanne"
    )
    building_names: dict[str, str] = Field(
        default_factory=dict, description="by building key (b0: Villa…)"
    )
    slab_thickness: float | None = Field(
        None,
        ge=0,
        le=3,
        description="the slab under the lowest storey (m), when the model has none",
    )
    datum: float | None = Field(None, description="altitude of the ground floor's ±0.00 (m)")
    plot_area: float | None = Field(None, ge=0, description="m², when the model has no plot")
    weights: Weights = Field(default_factory=Weights)
    prices: Prices = Field(default_factory=Prices)
    terraces: dict[str, str] = Field(
        default_factory=dict, description="terrace index (as the scene lists them) → its flat"
    )
    gardens: dict[str, float] = Field(
        default_factory=dict, description="flat → private garden (m²)"
    )


class ReportDefaults(BaseModel):
    """What the report fills in when the owner has not: from the plans and the surroundings."""

    parcel: str | None = Field(
        None, description="from the surroundings' parcel or the plans' title block"
    )
    north: float | None = Field(
        None, description="degrees the scene is turned clockwise (seen from above), when aligned"
    )
    datum: float | None = Field(None, description="altitude of the scene's y = 0, when aligned")


class ReportOut(BaseModel):
    settings: ReportSettings
    defaults: ReportDefaults


class PdfRequest(BaseModel):
    html: str = Field(description="the report as a standalone page (styles and fonts inline)")
    filename: str = Field("rapport-sia416.pdf", max_length=200)
