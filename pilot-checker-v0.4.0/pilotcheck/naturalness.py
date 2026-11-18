"""Protocol-v1 arithmetic for explicitly synthetic or future aggregated task logs.

No browser access, log collection, remapping, or Tier-2 success conversion.
"""
import math


def score_counts(*, total, trigger, matched, unknown_focus, minutes, sessions,
                 complete=True, detected=None, detection_trials=None, detection_complete=True):
    for value in (total, trigger, matched, unknown_focus, sessions):
        if type(value) is not int or value < 0:
            raise ValueError('Counts must be nonnegative integers')
    if trigger > total or matched + unknown_focus > trigger:
        raise ValueError('Inconsistent nested event counts')
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes < 0:
        raise ValueError('Minutes must be finite and nonnegative')
    if type(complete) is not bool or type(detection_complete) is not bool:
        raise ValueError('Completeness must be boolean')
    if (detected is None) != (detection_trials is None):
        raise ValueError('Detection numerator and denominator must both be present')
    if detected is not None and (type(detected) is not int or type(detection_trials) is not int
                                or not 0 <= detected <= detection_trials):
        raise ValueError('Invalid detection counts')
    usable = complete and total > 0
    point = usable and unknown_focus == 0
    return {
        'protocol_version': 'naturalness-v1',
        'status': 'complete-counts' if point else 'unmeasured-or-incomplete',
        'trigger_frequency': trigger / total if usable else None,
        'focus_given_trigger': matched / trigger if point and trigger else None,
        'opportunities_per_1000_keys': 1000 * matched / total if point else None,
        'opportunities_per_minute': matched / minutes if point and minutes > 0 else None,
        'mean_opportunities_per_session': matched / sessions if point and sessions else None,
        'observed_scope_bounds_per_1000': [1000 * matched / total, 1000 * (matched + unknown_focus) / total] if usable else None,
        'stealth': 100 * (1 - detected / detection_trials) if detection_complete and detection_trials else None,
    }
