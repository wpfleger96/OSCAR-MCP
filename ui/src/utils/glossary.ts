export interface GlossaryEntry {
    label: string
    short: string // one-sentence explanation
    long?: string // optional fuller detail
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
    session_duration_hours: {
        label: 'Duration',
        short: 'Total hours of CPAP therapy recorded in this session.',
    },
    total_breaths: {
        label: 'Total Breaths',
        short: 'Number of detected complete breath cycles during the session.',
    },
    machine_events: {
        label: 'Machine Events',
        short: 'Respiratory events flagged by the CPAP device firmware in real time.',
        long: 'Machine events use proprietary device algorithms and may differ from SNORE’s programmatic detections.',
    },
    pulse_change_count: {
        label: 'Pulse Changes',
        short: 'Number of pulse-rate change events detected, used as arousal markers.',
    },
    programmatic_events: {
        label: 'Programmatic Events',
        short: "Respiratory events detected by SNORE's own analysis algorithms from the raw flow signal.",
    },
    ahi: {
        label: 'AHI',
        short: 'Apnea-Hypopnea Index: total apneas and hypopneas per hour of therapy.',
        long: 'AHI = (apneas + hypopneas) / hours. Common clinical thresholds: <5 normal, 5–15 mild, 15–30 moderate, >30 severe.',
    },
    rdi: {
        label: 'RDI',
        short: 'Respiratory Disturbance Index: AHI plus RERAs per hour.',
        long: 'RDI is always ≥ AHI; a large gap suggests airway effort and arousals without frank apneas.',
    },
    rei: {
        label: 'REI',
        short: 'Respiratory Event Index: respiratory events per hour of recorded device time (used when sleep time is not measured).',
    },
    oai: {
        label: 'OAI',
        short: 'Obstructive Apnea Index: obstructive apneas per hour of therapy.',
    },
    cai: {
        label: 'CAI',
        short: 'Central Apnea Index: central apneas per hour of therapy.',
    },
    hi: {
        label: 'HI',
        short: 'Hypopnea Index: hypopneas per hour of therapy.',
    },
    rera: {
        label: 'RERA Index',
        short: 'Respiratory Effort-Related Arousals per hour of therapy.',
    },
    apneas: {
        label: 'Apneas',
        short: 'Total apnea events: obstructive, central, and mixed combined.',
    },
    obstructive_apneas: {
        label: 'Obstructive Apneas',
        short: 'Airway blocked by soft-tissue collapse while breathing effort continues; airflow drops ≥90% for ≥10 s.',
    },
    central_apneas: {
        label: 'Central Apneas',
        short: 'Breathing pauses where the brain stops signaling breathing effort; no airflow and no effort for ≥10 s.',
    },
    mixed_apneas: {
        label: 'Mixed Apneas',
        short: 'Apneas that begin central (no effort) and end obstructive (effort against a blocked airway).',
    },
    hypopneas: {
        label: 'Hypopneas',
        short: 'Partial airway obstructions with roughly ≥30% flow reduction lasting ≥10 s.',
    },
    reras: {
        label: 'RERAs',
        short: 'Respiratory Effort-Related Arousals: flow-limited breathing that causes an arousal without meeting apnea or hypopnea criteria.',
    },
    flow_limitations: {
        label: 'Flow Limitations',
        short: 'Breaths or periods where the inspiratory airflow shape is flattened by partial airway narrowing.',
    },
    event_type_oa: {
        label: 'OA — Obstructive Apnea',
        short: 'Airway blocked by soft-tissue collapse; airflow drops ≥90% for ≥10 s while breathing effort continues.',
    },
    event_type_ca: {
        label: 'CA — Central Apnea',
        short: 'Brain stops signaling breathing effort; no airflow and no effort for ≥10 s.',
    },
    event_type_ma: {
        label: 'MA — Mixed Apnea',
        short: 'Begins as a central apnea (no effort) and ends obstructive (effort against a blocked airway).',
    },
    event_type_h: {
        label: 'H — Hypopnea',
        short: 'Partial airway obstruction with roughly ≥30% flow reduction lasting ≥10 s.',
    },
    event_type_re: {
        label: 'RE — RERA',
        short: 'Flow-limited breathing that causes an arousal without meeting apnea or hypopnea criteria.',
    },
    event_type_fl: {
        label: 'FL — Flow Limitation',
        short: 'Inspiratory airflow shape flattened by partial airway narrowing without reaching apnea/hypopnea thresholds.',
    },
    pressure: {
        label: 'Pressure',
        short: 'Therapy air pressure delivered by the device, in cmH₂O.',
        long: 'Min/median/95th-percentile/max variants summarize the pressure distribution across the night.',
    },
    epap: {
        label: 'EPAP',
        short: 'Expiratory Positive Airway Pressure: pressure delivered during exhalation, in cmH₂O.',
    },
    ipap: {
        label: 'IPAP',
        short: 'Inspiratory Positive Airway Pressure: pressure delivered during inhalation, in cmH₂O (bilevel modes).',
    },
    leak: {
        label: 'Leak',
        short: 'Unintentional air leak rate from the mask, in L/min.',
        long: 'Sustained large leaks reduce therapy effectiveness and can hide events from detection. Percentile variants summarize the night’s leak distribution.',
    },
    spo2: {
        label: 'SpO₂',
        short: 'Blood oxygen saturation from the pulse oximeter, in percent.',
    },
    spo2_drop: {
        label: 'SpO₂ Drop',
        short: 'Decrease in blood oxygen saturation during the event window.',
    },
    spo2_below_90: {
        label: 'SpO₂ Below 90%',
        short: 'Total time with oxygen saturation under 90%.',
    },
    pulse: {
        label: 'Pulse',
        short: 'Heart rate from the pulse oximeter, in beats per minute.',
    },
    resp_rate: {
        label: 'Respiratory Rate',
        short: 'Breaths per minute.',
    },
    tidal_volume: {
        label: 'Tidal Volume',
        short: 'Estimated air volume moved per breath, in mL.',
    },
    mv: {
        label: 'Minute Ventilation',
        short: 'Total air volume breathed per minute (tidal volume × respiratory rate), in L/min.',
    },
    peak_fl: {
        label: 'Peak Flow Limitation',
        short: 'Highest flow-limitation score recorded during the event window (0–1 scale).',
    },
    usage: {
        label: 'Usage',
        short: 'Hours of therapy use.',
    },
    flow_reduction: {
        label: 'Flow Reduction',
        short: 'Estimated fraction of baseline inspiratory flow lost during the event.',
    },
    confidence: {
        label: 'Confidence',
        short: 'Algorithm confidence that this event meets its classification criteria.',
        long: 'Events near classification thresholds get lower confidence and warrant more skepticism.',
    },
    avg_confidence: {
        label: 'Avg Confidence',
        short: 'Average breath-classification confidence across all breaths in the session.',
    },
    csr: {
        label: 'Cheyne-Stokes Respiration',
        short: 'Cyclical waxing-and-waning breathing with central pauses, associated with cardiac or neurological conditions.',
    },
    periodic_breathing: {
        label: 'Periodic Breathing',
        short: 'Repeating cycles of breathing and pauses; broader than CSR and also seen at altitude.',
    },
    flow_limitation_index: {
        label: 'Flow Limitation Index',
        short: 'Severity-weighted share of breaths showing flow limitation.',
        long: "Each breath's class weight (0.0 for Class 1 up to 1.0 for Class 7) is multiplied by its classification confidence, then averaged across all breaths.",
    },
    false_negatives: {
        label: 'False Negatives',
        short: "Machine-flagged events that SNORE's analysis did not detect.",
    },
    false_positives: {
        label: 'False Positives',
        short: "SNORE-detected events absent from the machine's event log.",
        long: 'May be real events the device missed, or over-detections by the algorithm.',
    },
    days_with_data: {
        label: 'Days with Data',
        short: 'Number of days in the period with at least one recorded session.',
    },
    effectiveness: {
        label: 'Effectiveness',
        short: 'Overall therapy quality rating derived from AHI: excellent, good, fair, or poor.',
    },
    ahi_trend: {
        label: 'AHI Trend',
        short: 'Direction of recent AHI change: improving, worsening, or stable.',
    },
    sensitivity: {
        label: 'Sensitivity',
        short: 'Share of machine-flagged events that SNORE also detected (true-positive rate / recall).',
    },
    precision: {
        label: 'Precision',
        short: 'Share of SNORE-detected events that match a machine-flagged event.',
    },
    f1: {
        label: 'F1 Score',
        short: 'Harmonic mean of sensitivity and precision; balances missed events against over-detection.',
    },

    // ── New device-channel labels ──────────────────────────────────────────
    fl_device: {
        label: 'Flow Limitation (device)',
        short: "ResMed's proprietary per-breath severity index for flow limitation, 0 (none) to 1 (severe).",
        long: "This is distinct from SNORE's computed flow-limitation classes. The device reports a continuous 0–1 score derived from its own internal algorithm; SNORE's FL classes are based on inspiratory flow-shape analysis.",
    },
    snore_device: {
        label: 'Snore (device)',
        short: 'ResMed device snore index, 0 (absent) to 5 (severe), sampled once per breath.',
        long: 'A unitless severity score derived from the high-frequency vibration component of mask pressure. Not equivalent to decibel snore measurements.',
    },
    ie_ratio_waveform: {
        label: 'I:E Ratio',
        short: 'Instantaneous inspiratory-to-expiratory time ratio (expressed as a percentage, where 100 = 1:1).',
    },
    ti_waveform: {
        label: 'Inspiratory Time',
        short: 'Duration of the inspiratory phase of each breath, in seconds.',
    },
    pressure_hr_waveform: {
        label: 'Mask Pressure (25 Hz)',
        short: '25 Hz mask-pressure signal providing finer time resolution than the standard 2 Hz pressure channel.',
        long: 'Useful for detecting brief snore vibrations and inspiratory flow-limitation shapes that are averaged away in the lower-resolution channel.',
    },
    trigger_cycle: {
        label: 'Trigger/Cycle (raw codes)',
        short: 'Raw numeric event codes (0–16) logged by the device firmware for breath trigger and cycle transitions.',
        long: 'These are undecoded manufacturer-internal event codes. They are stored as-is and have not been mapped to named states. Consult ResMed documentation or OSCAR source for code-to-state mappings.',
    },

    // ── STR daily statistics ───────────────────────────────────────────────
    uai: {
        label: 'UAI',
        short: 'Unintentional Apnea Index reported by the device firmware; apneas per hour (device-computed).',
    },
    ai_str: {
        label: 'AI',
        short: 'Apnea Index reported by the device; total apneas (OA + CA) per hour of therapy.',
    },
    rin: {
        label: 'RIN',
        short: 'Respiratory Intensity Index; device-reported measure of respiratory event intensity (events/hr).',
    },
    spont_cyc_pct: {
        label: 'Spont Cyc %',
        short: 'Percentage of breaths that cycled spontaneously (patient-triggered expiration) in VAuto/bilevel modes.',
    },
    mask_events_str: {
        label: 'Mask Events',
        short: 'Number of mask-on events logged by the device during the session.',
    },
    flow_5th: {
        label: 'Flow 5th Percentile',
        short: '5th percentile of instantaneous flow. Typically negative because expiratory flow is represented as a negative value.',
        long: 'This value is expected to be negative for healthy breathing — it represents the lower tail of the flow distribution where expiratory flow dominates. A large negative value is normal, not an error.',
    },
    ie_ratio_stat: {
        label: 'I:E Ratio',
        short: 'Daily I:E ratio percentile from the STR summary (percent; 100 = 1:1, 50 = 1:2). VAuto devices only.',
        long: 'Values above 100 indicate inspiration longer than expiration (I > E), which is normal on bilevel and VAuto devices.',
    },
    ti_stat: {
        label: 'Inspiratory Time (Ti)',
        short: 'Inspiratory phase duration percentile from the STR summary, in seconds. VAuto devices only.',
    },
    amb_humidity: {
        label: 'Ambient Humidity',
        short: 'Median ambient relative humidity recorded by the device humidifier sensor, in percent.',
    },
    hum_temp: {
        label: 'Humidifier Temperature',
        short: 'Median humidifier chamber temperature, in °C.',
    },

    // ── Blower-side flow & pressure (STR percentiles) ─────────────────────
    blow_press: {
        label: 'Blower Pressure',
        short: 'Pressure measured at the blower output, upstream of the humidifier and tubing, in cmH₂O.',
        long: 'Differs from mask pressure: blower pressure is higher because it has not yet dropped across the hose and humidifier. Percentile variants summarize the nightly distribution.',
    },
    blow_flow: {
        label: 'Blower Flow',
        short: 'Airflow measured at the blower output, in L/min. Includes both patient flow and intentional leak.',
    },

    // ── Climate & humidifier power ────────────────────────────────────────
    htube_temp: {
        label: 'Heated Tube Temperature',
        short: 'Median heated-tube (ClimateLine) temperature, in °C.',
    },
    htube_pow: {
        label: 'Heated Tube Power',
        short: 'Median power delivered to the heated tube, in watts.',
    },
    hum_pow: {
        label: 'Humidifier Power',
        short: 'Median power delivered to the humidifier heater plate, in watts.',
    },

    // ── Apple Health sleep metrics ────────────────────────────────────────
    time_in_bed: {
        label: 'Time in Bed',
        short: 'Total recorded time from first InBed sample to last, in hours.',
    },
    total_sleep: {
        label: 'Total Sleep',
        short: 'Total time actually asleep (Core + Deep + REM stages combined), in hours.',
    },
    sleep_efficiency: {
        label: 'Sleep Efficiency',
        short: 'Total sleep divided by time in bed, expressed as a percentage.',
        long: 'Sleep efficiency = total sleep / time in bed × 100. Values below 85% may indicate difficulty falling or staying asleep.',
    },
    core_sleep: {
        label: 'Core Sleep',
        short: "Apple Health's Core stage corresponds to NREM N1 and N2 light sleep combined.",
        long: "Core sleep (N1 + N2) is the most common stage and forms the backbone of each sleep cycle. Apple Health labels light non-REM sleep as 'Core'.",
    },
    deep_sleep: {
        label: 'Deep Sleep',
        short: 'NREM N3 slow-wave sleep — the most restorative stage.',
        long: 'Deep sleep supports physical repair and immune function. It is most concentrated in the first half of the night and decreases with age.',
    },
    rem_sleep: {
        label: 'REM Sleep',
        short: 'Rapid Eye Movement sleep, associated with dreaming and memory consolidation.',
    },
    awake_time: {
        label: 'Awake',
        short: 'Time spent awake after initial sleep onset, as detected by Apple Health.',
    },

    // ── Primary waveform channels ─────────────────────────────────────────
    flow: {
        label: 'Flow Rate',
        short: 'Bidirectional inspiratory/expiratory airflow in L/min, sampled at 25 Hz.',
        long: 'Positive values are inspiratory; negative values are expiratory. This is the primary signal used for breath detection and event scoring.',
    },
    therapy_pressure: {
        label: 'Therapy Pressure',
        short: 'Therapy-algorithm target pressure in cmH₂O, reported as a 0.5 Hz duty-cycle average.',
        long: "Distinct from the mask-side 'Pressure' channel: this is the algorithm's commanded set point rather than the measured delivered pressure.",
    },
}
