# Test Plan: Alert Logic

This markdown test plan enumerates scenarios and expected outcomes for the orchestrator's decision logic and metrics.

## Assumptions

- Global safety factor `c=0.5`.
- Timezone `Asia/Tokyo`.
- Assets: `[djed, usdm]`.
- Reference keyword: `alert_driven_withdrawal`.

## Scenarios

1) Positive gains (WITHDRAW_OK for DJED)
- Given `V_djed(t0)=1000`, `V_djed(t1)=1120`, `p_djed(t1)=1`, then:
  - `G_djed=120`, `W_{djed,max,usd}=60` -> decision=1
  - Metrics: `wo_decision{asset="djed"}=1`, `wo_wmax_usd{asset="djed"}=60`, `wo_g_usd{asset="djed"}=120`, `wo_price_t1_usd{asset="djed"}=1`

2) Non-positive gains (HOLD for USDM)
- Given `V_usdm(t0)=1000`, `V_usdm(t1)=995`, `p_usdm(t1)=1`, then:
  - `G_usdm=-5`, `W_{usdm,max,usd}=0` -> decision=0
  - Metrics: `wo_decision{asset="usdm"}=0`, `wo_wmax_usd{asset="usdm"}=0`, `wo_g_usd{asset="usdm"}=-5`

3) Missing assets list (ERROR)
- Given `client.assets=[]` (or missing):
  - Skip evaluation; metrics: `wo_decision{asset="_all_"}=-1`, no per-asset metrics.

4) Partial data failure (ERROR for one asset)
- If price for `usdm` is missing at `t1`:
  - `wo_decision{asset="usdm"}=-1`; `djed` remains evaluated normally.

5) Timezone correctness
- With timezone set to `Asia/Tokyo`, ensure evaluation `t1` corresponds to local "now" and timestamps are stored as timezone-aware datetimes.

6) Smoothing application
- When smoothing config sets polynomial order=2 for `djed`, verify that gains computation uses smoothed series consistently with the client behavior (if applicable to the chosen calculation path).

7) Reference update semantics
- After a user-confirmed withdrawal is recorded (to test tables), `V_i(t0)` rolls forward to the new reference. Subsequent evaluations must use the new reference.

## Non-goals

- No chart/report validation (out of scope).
- No webhook delivery (Phase D).
