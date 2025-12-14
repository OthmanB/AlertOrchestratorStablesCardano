#!/usr/bin/env python3
"""
Correct gain calculation implementation based on mini_test.py logic

This implements the mathematically correct approach:
G(t) = P(t) - P(t₀) - CDF(D(t,t₀)) + CDF(W(t,t₀))

Where:
- P(t) = position at time t (interpolated for missing values)
- P(t₀) = reference position at time t₀ 
- CDF(D) = cumulative deposit function from t₀ to t
- CDF(W) = cumulative withdrawal function from t₀ to t

This replaces the flawed derivative and complex synchronization approaches
with the mathematically proven correct formula from mini_test.py.
"""
import numpy as np
from typing import List, Tuple, Optional
from datetime import datetime, timezone
import logging
from .models import Transaction

logger = logging.getLogger(__name__)


def create_unified_timebase(
    position_timestamps: List[datetime],
    transaction_timestamps: List[datetime]
) -> np.ndarray:
    """
    Create unified timebase as union of all timestamp vectors
    
    Args:
        position_timestamps: Timestamps where positions are available
        transaction_timestamps: Timestamps where transactions occurred
        
    Returns:
        Sorted unified timebase as numpy array
    """
    # Normalize all timestamps to UTC (make them timezone-aware)
    def normalize_timestamp(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    
    # Normalize position timestamps
    normalized_position = [normalize_timestamp(dt) for dt in position_timestamps]
    
    # Normalize transaction timestamps  
    normalized_transaction = [normalize_timestamp(dt) for dt in transaction_timestamps]
    
    # Create union of all timestamps
    all_timestamps = set(normalized_position + normalized_transaction)
    unified_timebase = np.array(sorted(all_timestamps))

    # Debug details on timebase
    if logger.isEnabledFor(logging.DEBUG):
        def ts_range(ts_list: List[datetime]):
            if not ts_list:
                return (None, None)
            s = sorted(ts_list)
            return (s[0], s[-1])

        pos_min, pos_max = ts_range(position_timestamps)
        tx_min, tx_max = ts_range(transaction_timestamps)
        uni_min = unified_timebase[0] if len(unified_timebase) else None
        uni_max = unified_timebase[-1] if len(unified_timebase) else None
        logger.debug(
            "Unified timebase created | pos=%d (min=%s, max=%s) tx=%d (min=%s, max=%s) unified=%d (min=%s, max=%s)",
            len(position_timestamps), pos_min, pos_max,
            len(transaction_timestamps), tx_min, tx_max,
            len(unified_timebase), uni_min, uni_max,
        )
    
    logger.info(
        "Created unified timebase: %d position timestamps + %d transaction timestamps = %d unified points",
        len(position_timestamps), len(transaction_timestamps), len(unified_timebase)
    )
    
    return unified_timebase


def interpolate_positions_on_timebase(
    position_timestamps: List[datetime],
    position_values: List[float],
    unified_timebase: np.ndarray,
    interpolation_method: str = "linear"
) -> np.ndarray:
    """
    Interpolate position values onto unified timebase
    
    Positions represent continuous growth (interest accrual), so interpolation
    is appropriate for estimating missing values between API snapshots.
    
    Args:
        position_timestamps: Original position timestamps
        position_values: Position values at those timestamps
        unified_timebase: Target timebase for interpolation
        interpolation_method: Method for interpolation ('linear' or 'cubic')
        
    Returns:
        Interpolated position values on unified timebase
    """
    if len(position_timestamps) != len(position_values):
        raise ValueError("Position timestamps and values must have same length")
    
    # Normalize timestamps to UTC
    def normalize_timestamp(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    
    normalized_position_timestamps = [normalize_timestamp(dt) for dt in position_timestamps]
    
    # Convert datetime to numeric for interpolation
    position_numeric = np.array([ts.timestamp() for ts in normalized_position_timestamps])
    unified_numeric = np.array([ts.timestamp() for ts in unified_timebase])
    
    # Interpolate based on method
    if interpolation_method == "linear":
        interpolated_positions = np.interp(unified_numeric, position_numeric, position_values)
    elif interpolation_method == "cubic":
        try:
            from scipy.interpolate import interp1d
            if len(position_values) < 4:
                logger.warning("Less than 4 points available, falling back to linear interpolation")
                interpolated_positions = np.interp(unified_numeric, position_numeric, position_values)
            else:
                # For cubic interpolation, use bounds_error=False and extrapolate manually
                f = interp1d(position_numeric, position_values, kind='cubic', bounds_error=False, fill_value=np.nan)
                interpolated_positions = f(unified_numeric)
                # Fill any NaN values with linear extrapolation
                nan_mask = np.isnan(interpolated_positions)
                if np.any(nan_mask):
                    interpolated_positions[nan_mask] = np.interp(
                        unified_numeric[nan_mask], position_numeric, position_values
                    )
        except ImportError:
            logger.warning("scipy not available, falling back to linear interpolation")
            interpolated_positions = np.interp(unified_numeric, position_numeric, position_values)
    else:
        raise ValueError(f"Unsupported interpolation method: {interpolation_method}")
    
    # Debug interpolation context
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "Interpolation | method=%s | pos_points=%d -> unified_points=%d | pos_time[min,max]=(%s,%s) | unified_time[min,max]=(%s,%s)",
            interpolation_method,
            len(position_values),
            len(unified_timebase),
            normalized_position_timestamps[0] if normalized_position_timestamps else None,
            normalized_position_timestamps[-1] if normalized_position_timestamps else None,
            unified_timebase[0] if len(unified_timebase) else None,
            unified_timebase[-1] if len(unified_timebase) else None,
        )
    logger.info(
        "Interpolated %d positions to %d points using %s method",
        len(position_values), len(unified_timebase), interpolation_method
    )
    
    return interpolated_positions


def create_transaction_vectors_on_timebase(
    transactions: List[Transaction],
    unified_timebase: np.ndarray,
    *,
    interpolated_positions: Optional[np.ndarray] = None,
    position_timestamps_set: Optional[set] = None,
    alignment_method: str = "none",
    window_bins: int = 8,
    z_threshold: float = 3.0,
    magnitude_band: Tuple[float, float] = (0.3, 1.5),
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Create deposit and withdrawal vectors on unified timebase with zero-padding
    
    Transactions are discrete Dirac functions (instantaneous events), so they
    cannot be interpolated. Where no transaction occurred, we fill with zeros.
    
    Args:
        transactions: List of Transaction objects
        unified_timebase: Target timebase
        
    Returns:
        Tuple of (deposit_vector, withdrawal_vector) on unified timebase
    """
    deposit_vector = np.zeros(len(unified_timebase))
    withdrawal_vector = np.zeros(len(unified_timebase))
    
    # Normalize alignment method
    method = (alignment_method or "none").strip().lower()
    allow_detect = bool(method == "detect_spike" and interpolated_positions is not None)

    # Precompute mask of indices that are original position samples (for snap methods)
    pos_mask = None
    if position_timestamps_set:
        try:
            pos_mask = np.array([tb in position_timestamps_set for tb in unified_timebase], dtype=bool)
        except Exception:
            pos_mask = None

    # Helper: choose index with alignment policy
    def choose_index_for_tx(idx: int, tx: Transaction) -> int:
        # Right-open: shift to idx+1 when possible
        if method == "right_open":
            return min(idx + 1, len(unified_timebase) - 1)
        # Snap to next original position timestamp (right edge of bin)
        if method == "snap_to_next_pos" and pos_mask is not None:
            # If base is already at a position sample, keep it, else advance to next True
            i = idx
            n = len(unified_timebase)
            while i < n and not pos_mask[i]:
                i += 1
            if i >= n:
                # Fallback: use last available position sample
                last_idx = int(np.where(pos_mask)[0][-1]) if pos_mask.any() else idx
                return last_idx
            return int(i)
        # Snap to previous original position timestamp (left edge of bin)
        if method == "snap_to_prev_pos" and pos_mask is not None:
            i = idx
            while i >= 0 and not pos_mask[i]:
                i -= 1
            if i < 0:
                # Fallback: first available position sample or base idx
                first_idx = int(np.where(pos_mask)[0][0]) if pos_mask.any() else idx
                return first_idx
            return int(i)
        # Snap to nearest original position timestamp (by time distance; tie -> right)
        if method == "snap_to_nearest_pos" and pos_mask is not None:
            if pos_mask.any():
                pos_indices = np.where(pos_mask)[0]
                # Compute time deltas to tx.timestamp
                try:
                    deltas = np.array([
                        abs((unified_timebase[k] - tx.timestamp).total_seconds()) for k in pos_indices
                    ])
                    if deltas.size > 0:
                        min_d = float(np.min(deltas))
                        # Right tie-break: choose the greatest index among minima
                        candidates = pos_indices[np.where(np.isclose(deltas, min_d))]
                        return int(np.max(candidates))
                except Exception:
                    # Fallback to index distance if any issue
                    return int(pos_indices[np.argmin(np.abs(pos_indices - idx))])
        # Detect spike: search for step in ΔP with correct sign/magnitude within window
        if allow_detect and interpolated_positions is not None and 0 < idx < len(unified_timebase):
            start = max(1, idx - window_bins)
            end = min(len(unified_timebase) - 1, idx + window_bins)
            dP = np.diff(interpolated_positions)
            local = dP[start:end + 1]
            if local.size > 0:
                med = float(np.median(local))
                mad = float(np.median(np.abs(local - med)))
                scale = 1.4826 * mad if mad > 0 else (np.std(local) if np.std(local) > 0 else 1.0)
                z = (local - med) / scale
                sign = -1.0 if tx.transaction_type == "withdrawal" else 1.0
                mag = abs(float(tx.amount))
                alpha, beta = magnitude_band

                def _scan(ignore_magnitude: bool = False):
                    best_idx = None
                    best_score = None
                    for j in range(local.size):
                        val = float(local[j])
                        zval = float(z[j])
                        # Sign and z-score gate
                        if (sign * val) <= 0 or abs(zval) < z_threshold:
                            continue
                        if not ignore_magnitude and mag > 0:
                            ratio = (abs(val) / mag) if mag != 0 else float('inf')
                            if not (alpha <= ratio <= beta):
                                continue
                        # prefer smallest |j - (idx-start)| and largest |z|
                        dist = abs((start + j) - idx)
                        score = (dist, -abs(zval))
                        if best_score is None or score < best_score:
                            best_score = score
                            best_idx = start + j
                    return best_idx

                best_i = _scan(ignore_magnitude=False)
                if best_i is None:
                    # Fallback: ignore magnitude band and try again (units may differ)
                    best_i = _scan(ignore_magnitude=True)
                if best_i is not None:
                    # Map to right edge of the detected step (ΔP at best_i applies to transition best_i -> best_i+1)
                    return int(min(best_i + 1, len(unified_timebase) - 1))
                # Final fallback: snap to next non-flat ΔP with expected sign within a forward window
                # This avoids long plateaus when the recorded transaction precedes the actual position step.
                max_forward = max(8, window_bins * 3)
                f_start = idx
                f_end = min(len(unified_timebase) - 2, idx + max_forward)
                if f_end > f_start:
                    forward_local = dP[f_start:f_end + 1]
                    if forward_local.size > 0:
                        eps = 1e-9
                        for j in range(forward_local.size):
                            val = float(forward_local[j])
                            if abs(val) > eps and (sign * val) > 0:
                                # Map to right edge of the forward-detected step
                                return int(min(f_start + j + 1, len(unified_timebase) - 1))
        # Default: no shift
        return int(idx)

    # Map transactions to timebase indices
    for transaction in transactions:
        # Find exact timestamp match in unified timebase
        matches = np.where(unified_timebase == transaction.timestamp)[0]
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Map tx -> timebase | type=%s amount=%s ts=%s tz=%s | matches=%d",
                transaction.transaction_type,
                f"{transaction.amount:.8f}",
                transaction.timestamp,
                getattr(transaction.timestamp, 'tzinfo', None),
                len(matches),
            )
        
        if len(matches) > 0:
            base_idx = int(matches[0])
            idx = choose_index_for_tx(base_idx, transaction)
            if transaction.transaction_type == "deposit":
                deposit_vector[idx] = transaction.amount
            elif transaction.transaction_type == "withdrawal":
                withdrawal_vector[idx] = abs(transaction.amount)  # Store as positive
            if logger.isEnabledFor(logging.DEBUG):
                # Show small neighborhood around the mapped index
                start = max(0, idx - 2)
                end = min(len(unified_timebase), idx + 3)
                nb_ts = unified_timebase[start:end]
                nb_w = withdrawal_vector[start:end]
                nb_d = deposit_vector[start:end]
                logger.debug(
                    "Tx mapped at idx=%d (base=%d, method=%s) | window[%d:%d) ts=%s | D=%s | W=%s | pos_at_idx=%s",
                    idx, base_idx, method,
                    start, end,
                    list(nb_ts),
                    [float(f"{x:.6f}") for x in nb_d.tolist()],
                    [float(f"{x:.6f}") for x in nb_w.tolist()],
                    bool(pos_mask[idx]) if pos_mask is not None else None,
                )
        else:
            # Log the closest indices (by absolute time difference) to diagnose alignment issues
            logger.warning("Transaction timestamp %s not found in unified timebase", transaction.timestamp)
            try:
                # Compute nearest timebase point deltas
                deltas = np.array([abs((tb - transaction.timestamp).total_seconds()) for tb in unified_timebase])
                near_idx = int(np.argmin(deltas)) if len(deltas) else None
                if near_idx is not None:
                    logger.debug(
                        "Closest unified ts at idx=%d ts=%s | delta=%.3fs",
                        near_idx, unified_timebase[near_idx], deltas[near_idx]
                    )
            except Exception as e:
                logger.debug("Failed to compute nearest unified timebase point: %s", e)
    
    total_deposits = np.sum(deposit_vector)
    total_withdrawals = np.sum(withdrawal_vector)
    logger.info(
        "Created transaction vectors: %.2f total deposits, %.2f total withdrawals",
        total_deposits, total_withdrawals
    )
    return deposit_vector, withdrawal_vector


def calculate_correct_gains(
    position_timestamps: List[datetime],
    position_values: List[float],
    transactions: List[Transaction],
    reference_time_index: int = 0,
    interpolation_method: str = "linear",
    alignment_method: str = "none",
    tx_timestamp_source: str = "timestamp",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Calculate gains using the correct formula from mini_test.py:
    G(t) = P(t) - P(t₀) - CDF(D(t,t₀)) + CDF(W(t,t₀))
    
    Args:
        position_timestamps: Timestamps where positions are sampled
        position_values: Position values at those timestamps  
        transactions: List of Transaction objects
        reference_time_index: Index in unified timebase to use as t₀ (default: 0)
        interpolation_method: Method for position interpolation
        
    Returns:
        Tuple of (unified_timebase, interpolated_positions, deposit_cdf, withdrawal_cdf, gains)
    """
    logger.info(
        "Starting correct gain calculation with %d positions and %d transactions",
        len(position_values), len(transactions)
    )
    
    # Step 1: Create unified timebase (union of all timestamps)
    # Optionally switch to created_at for alignment if configured
    use_created = (str(tx_timestamp_source or "timestamp").strip().lower() == "created_at")
    transaction_timestamps = [getattr(tx, 'created_at', tx.timestamp) if use_created else tx.timestamp for tx in transactions]
    unified_timebase = create_unified_timebase(position_timestamps, transaction_timestamps)
    
    # Step 2: Interpolate positions onto unified timebase
    interpolated_positions = interpolate_positions_on_timebase(
        position_timestamps, position_values, unified_timebase, interpolation_method
    )
    
    # Step 3: Create transaction vectors with zero-padding
    # Normalize position timestamps to UTC and build a set for snap alignment
    def _norm(dt):
        from datetime import timezone
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    pos_ts_set = set(_norm(ts) for ts in position_timestamps)
    # If using created_at for alignment, create shallow copies with timestamp swapped
    if use_created:
        txs_for_map = []
        for tx in transactions:
            try:
                # Make a simple proxy with timestamp set to created_at
                from dataclasses import replace
                txs_for_map.append(replace(tx, timestamp=getattr(tx, 'created_at', tx.timestamp)))
            except Exception:
                txs_for_map.append(tx)
    else:
        txs_for_map = transactions
    deposit_vector, withdrawal_vector = create_transaction_vectors_on_timebase(
        txs_for_map, unified_timebase,
        interpolated_positions=interpolated_positions,
        position_timestamps_set=pos_ts_set,
        alignment_method=alignment_method if isinstance(alignment_method, str) else "none",
    )
    
    # Step 4: Calculate cumulative deposit and withdrawal functions (CDF)
    deposit_cdf = np.cumsum(deposit_vector)
    withdrawal_cdf = np.cumsum(withdrawal_vector)
    
    # Step 5: Calculate gains using correct formula
    # G(t) = P(t) - P(t₀) - CDF(D(t,t₀)) + CDF(W(t,t₀))
    
    if reference_time_index >= len(unified_timebase):
        raise ValueError(f"Reference time index {reference_time_index} out of bounds")
    
    P_t0 = interpolated_positions[reference_time_index]
    CDF_D_t0 = deposit_cdf[reference_time_index] 
    CDF_W_t0 = withdrawal_cdf[reference_time_index]
    
    gains = (interpolated_positions - P_t0 - 
            (deposit_cdf - CDF_D_t0) + 
            (withdrawal_cdf - CDF_W_t0))
    
    logger.info("Calculated gains: range [%.2f, %.2f]", float(np.min(gains)), float(np.max(gains)))
    logger.info("Reference point: P(t₀)=%.2f at %s", float(P_t0), unified_timebase[reference_time_index])

    # Detailed debug around each withdrawal to diagnose spikes/dips
    if logger.isEnabledFor(logging.DEBUG):
        # Helper to find nearest original position timestamps around a given ts
        def _nearest_pos_neighbors(ts: datetime, pos_ts: List[datetime], pos_vals: List[float]):
            prev_t = None
            prev_v = None
            next_t = None
            next_v = None
            for i, t in enumerate(pos_ts):
                if t <= ts:
                    prev_t = t
                    prev_v = float(pos_vals[i])
                if t > ts:
                    next_t = t
                    next_v = float(pos_vals[i])
                    break
            return prev_t, prev_v, next_t, next_v

        # Log a compact diagnostic for every transaction (both deposits and withdrawals)
        exact_matches = 0
        no_matches = 0
        for tx in transactions:
            m = np.where(unified_timebase == tx.timestamp)[0]
            idx = int(m[0]) if len(m) else None
            if idx is not None:
                exact_matches += 1
                # Local window and step sizes around the transaction index
                p_here = float(interpolated_positions[idx])
                d_here = float(deposit_cdf[idx])
                w_here = float(withdrawal_cdf[idx])
                g_here = float(gains[idx])
                d_step = float(d_here - (deposit_cdf[idx - 1] if idx > 0 else 0.0))
                w_step = float(w_here - (withdrawal_cdf[idx - 1] if idx > 0 else 0.0))
                p_step_prev = float(p_here - (interpolated_positions[idx - 1] if idx > 0 else p_here))
                p_step_next = float((interpolated_positions[idx + 1] if idx + 1 < len(interpolated_positions) else p_here) - p_here)
                start = max(0, idx - 2)
                end = min(len(unified_timebase), idx + 3)
                logger.debug(
                    "Tx diag | type=%s amt=%.8f ts=%s created=%s dt_created=%.3fs | idx=%d | P=%.6f ΔP[-1]=%.6f ΔP[+1]=%.6f | ΔD=%.6f ΔW=%.6f | G=%.6f",
                    tx.transaction_type,
                    float(tx.amount),
                    tx.timestamp,
                    getattr(tx, 'created_at', None),
                    (getattr(tx, 'created_at', tx.timestamp) - tx.timestamp).total_seconds() if getattr(tx, 'created_at', None) else 0.0,
                    idx,
                    p_here,
                    p_step_prev,
                    p_step_next,
                    d_step,
                    w_step,
                    g_here,
                )
                # Also show the local window values for visual verification
                rows = []
                for i in range(start, end):
                    rows.append({
                        "i": i,
                        "ts": unified_timebase[i],
                        "P": float(interpolated_positions[i]),
                        "D_cdf": float(deposit_cdf[i]),
                        "W_cdf": float(withdrawal_cdf[i]),
                        "G": float(gains[i]),
                    })
                logger.debug("Window around tx idx=%d | rows=%s", idx, rows)
            else:
                no_matches += 1
                # Find nearest unified timebase point and distance
                try:
                    deltas = np.array([abs((tb - tx.timestamp).total_seconds()) for tb in unified_timebase])
                    near_idx = int(np.argmin(deltas)) if len(deltas) else None
                    near_ts = unified_timebase[near_idx] if near_idx is not None else None
                    near_dt = float(deltas[near_idx]) if near_idx is not None else None
                except Exception:
                    near_idx, near_ts, near_dt = None, None, None
                logger.debug(
                    "Tx diag | type=%s amt=%.8f ts=%s created=%s | no exact match | nearest idx=%s ts=%s dt=%.3fs",
                    tx.transaction_type,
                    float(tx.amount),
                    tx.timestamp,
                    getattr(tx, 'created_at', None),
                    str(near_idx),
                    near_ts,
                    near_dt if near_dt is not None else float('nan'),
                )

            # Report neighbors from original position samples for context
            prev_t, prev_v, next_t, next_v = _nearest_pos_neighbors(tx.timestamp, position_timestamps, position_values)
            logger.debug(
                "Pos neighbors @tx | prev=(%s, %s) next=(%s, %s)",
                prev_t, f"{prev_v:.6f}" if prev_v is not None else None,
                next_t, f"{next_v:.6f}" if next_v is not None else None,
            )

        logger.debug("Tx mapping summary | exact_matches=%d no_matches=%d total=%d", exact_matches, no_matches, len(transactions))
    
    return unified_timebase, interpolated_positions, deposit_cdf, withdrawal_cdf, gains


def calculate_correct_adjusted_positions(
    asset_symbol: str,
    supply_data: List[Tuple[datetime, float]], 
    transactions: List[Transaction],
    reference_time_index: int = 0,
    interpolation_method: str = "linear"
) -> List:
    """
    Calculate adjusted positions using the correct mini_test.py logic
    
    This replaces the flawed derivative and synchronized calculation approaches
    with the mathematically correct formula: G(t) = P(t) - P(t₀) - CDF(D) + CDF(W)
    
    Returns results in AdjustedSupplyPosition format for compatibility with existing code.
    
    Args:
        asset_symbol: Asset symbol being analyzed
        supply_data: List of (timestamp, position_value) tuples
        transactions: List of Transaction objects
        reference_time_index: Index in unified timebase to use as t₀ (default: 0)
        interpolation_method: Method for position interpolation
        
    Returns:
        List of AdjustedSupplyPosition objects using correct calculations
    """
    if not supply_data:
        return []
    
    # Import here to avoid circular imports
    from .models import AdjustedSupplyPosition
    
    # Extract position data
    position_timestamps = [ts for ts, _ in supply_data]
    position_values = [pos for _, pos in supply_data]
    
    # Calculate using correct method
    timebase, positions, deposits_cdf, withdrawals_cdf, gains = calculate_correct_gains(
        position_timestamps=position_timestamps,
        position_values=position_values,
        transactions=transactions,
        reference_time_index=reference_time_index,
        interpolation_method=interpolation_method
    )
    
    # Convert to AdjustedSupplyPosition format for backward compatibility
    adjusted_positions = []
    original_timestamps = set(position_timestamps)
    
    for i, timestamp in enumerate(timebase):
        # Only include original position timestamps in output (not interpolated ones)
        if timestamp in original_timestamps:
            # Calculate values for model compatibility
            cumulative_deposits = deposits_cdf[i]
            cumulative_withdrawals = withdrawals_cdf[i]
            true_gain = gains[i]
            
            # For model compatibility: Create adjusted_position using old formula
            # This allows existing code to work while we provide the true gain separately
            cumulative_investment = cumulative_deposits - cumulative_withdrawals  
            model_adjusted_position = positions[i] - cumulative_investment
            
            adjusted_positions.append(AdjustedSupplyPosition(
                timestamp=timestamp,
                asset_symbol=asset_symbol,
                raw_position=positions[i],
                adjusted_position=true_gain,  # PUT THE TRUE GAIN IN adjusted_position field
                cumulative_deposits=cumulative_deposits,
                cumulative_withdrawals=-cumulative_withdrawals,  # Model expects negative withdrawals
                net_gain=cumulative_investment  # Store investment for compatibility
            ))
            
    logger.info(f"Converted {len(timebase)} calculated points to {len(adjusted_positions)} adjusted positions")
    return adjusted_positions
