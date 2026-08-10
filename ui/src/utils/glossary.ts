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
}
