/**
 * Flow limitation class definitions mirroring FLOW_LIMITATION_CLASSES in src/snore/constants.py.
 */

export interface FlowLimitationClassInfo {
    name: string
    description: string
    visualCharacteristics: string
    clinicalSignificance: string
    severity: string
    weight: number
}

export const FLOW_LIMITATION_CLASSES: Record<number, FlowLimitationClassInfo> = {
    1: {
        name: 'Sinusoidal',
        description: 'Normal, rounded inspiration with smooth sinusoidal curve',
        visualCharacteristics: 'Smooth rounded peak, symmetric rise and fall',
        clinicalSignificance: 'Healthy unobstructed breathing pattern',
        severity: 'normal',
        weight: 0.0,
    },
    2: {
        name: 'Double Peak',
        description: 'Two distinct peaks during inspiration phase',
        visualCharacteristics: 'Two separate peaks with valley between, soft tissue vibration',
        clinicalSignificance:
            'Mild flow limitation - upper airway reopening after initial collapse',
        severity: 'mild',
        weight: 0.3,
    },
    3: {
        name: 'Flattened with Multiple Peaks',
        description: 'Multiple tiny peaks across flattened inspiratory curve',
        visualCharacteristics: 'Many small peaks/oscillations, irregular amplitude',
        clinicalSignificance:
            'Mild-moderate flow limitation - soft tissue vibration during inspiration',
        severity: 'mild-moderate',
        weight: 0.4,
    },
    4: {
        name: 'Peak During Initial Phase',
        description: 'Early sharp peak followed by sustained plateau',
        visualCharacteristics: 'Peak in first 30% of inspiration, then flat plateau',
        clinicalSignificance:
            'Moderate flow limitation - initial opening followed by restricted flow',
        severity: 'moderate',
        weight: 0.6,
    },
    5: {
        name: 'Peak at Midpoint',
        description: 'Single peak at midpoint with plateaus on both sides',
        visualCharacteristics: 'Central peak (40-60% position), flat on both sides',
        clinicalSignificance: 'Moderate-severe flow limitation - intensive phasic muscle activity',
        severity: 'moderate-severe',
        weight: 0.7,
    },
    6: {
        name: 'Peak During Late Phase',
        description: 'Initial plateau with late-phase peak',
        visualCharacteristics: 'Flat early phase, peak in final 30% of inspiration',
        clinicalSignificance:
            'Severe flow limitation - marked tracheal support during lung inflation',
        severity: 'severe',
        weight: 0.9,
    },
    7: {
        name: 'Plateau Throughout',
        description: 'Nearly flat plateau throughout entire inspiration',
        visualCharacteristics: 'Minimal amplitude variation, flat-top waveform throughout',
        clinicalSignificance: 'Severe flow limitation - collapsed noncompliant upper airway',
        severity: 'severe',
        weight: 1.0,
    },
}
