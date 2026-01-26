from .const import VIETNAM_ECOST_STAGES, VIETNAM_ECOST_VAT

def calc_ecost(kwh: float) -> int:
    """Calculate electric cost based on e-consumption"""

    if kwh <= 0:
        return 0

    total_price = 0.0

    e_stage_list = sorted(VIETNAM_ECOST_STAGES.keys())

    for index, e_stage in enumerate(e_stage_list):
        if kwh < e_stage:
            break

        if e_stage == e_stage_list[-1]:
            total_price += (kwh - e_stage) * VIETNAM_ECOST_STAGES[e_stage]
        else:
            next_stage = e_stage_list[index + 1]
            total_price += (
                (next_stage - e_stage)
                if kwh > next_stage
                else (kwh - e_stage)
            ) * VIETNAM_ECOST_STAGES[e_stage]

    total_price = int(
        round((total_price / 100) * (100 + VIETNAM_ECOST_VAT))
    )

    return total_price
    
def parse_evnhanoi_money(val: str | None) -> int | None:
    if not val:
        return None
    try:
        return int(val.replace(".", "").replace(",", ""))
    except Exception:
        return None
