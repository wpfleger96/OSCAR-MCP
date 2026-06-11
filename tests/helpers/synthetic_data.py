"""
Synthetic test data generators for breath waveforms and sessions.

Provides functions to generate controlled, reproducible test data for unit testing.
"""

import numpy as np


def generate_sinusoidal_breath(
    duration: float = 4.0,
    amplitude: float = 30.0,
    sample_rate: float = 25.0,
    baseline: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a perfect sinusoidal breath waveform.

    Args:
        duration: Breath duration in seconds
        amplitude: Peak flow amplitude in L/min
        sample_rate: Sample rate in Hz
        baseline: Baseline offset in L/min

    Returns:
        Tuple of (timestamps, flow_values)
    """
    n_samples = int(duration * sample_rate)
    timestamps = np.linspace(0, duration, n_samples)

    # Sinusoidal breath: positive (inspiration), negative (expiration)
    flow_values = amplitude * np.sin(2 * np.pi * timestamps / duration)
    flow_values += baseline

    return timestamps, flow_values


def generate_noisy_breath(
    duration: float = 4.0,
    amplitude: float = 30.0,
    sample_rate: float = 25.0,
    snr_db: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a breath waveform with controlled noise level.

    Args:
        duration: Breath duration in seconds
        amplitude: Peak flow amplitude in L/min
        sample_rate: Sample rate in Hz
        snr_db: Signal-to-noise ratio in dB

    Returns:
        Tuple of (timestamps, flow_values)
    """
    timestamps, clean_signal = generate_sinusoidal_breath(
        duration, amplitude, sample_rate
    )

    signal_power = np.mean(clean_signal**2)
    snr_linear = 10 ** (snr_db / 10)
    noise_power = signal_power / snr_linear
    noise_std = np.sqrt(noise_power)

    noise = np.random.normal(0, noise_std, len(clean_signal))
    noisy_signal = clean_signal + noise

    return timestamps, noisy_signal


def generate_flattened_breath(
    duration: float = 4.0,
    amplitude: float = 30.0,
    sample_rate: float = 25.0,
    flatness_index: float = 0.7,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a flow-limited breath with specified flatness.

    Creates a waveform with flattened inspiratory phase to simulate
    flow limitation.

    Args:
        duration: Breath duration in seconds
        amplitude: Peak flow amplitude in L/min
        sample_rate: Sample rate in Hz
        flatness_index: Target flatness (0=sinusoidal, 1=perfectly flat)

    Returns:
        Tuple of (timestamps, flow_values)
    """
    n_samples = int(duration * sample_rate)
    timestamps = np.linspace(0, duration, n_samples)

    half = n_samples // 2

    insp_t = np.linspace(0, np.pi, half)
    insp_flow = amplitude * np.sin(insp_t)

    plateau_threshold = amplitude * (1 - flatness_index)
    insp_flow = np.where(insp_flow > plateau_threshold, amplitude, insp_flow)

    exp_t = np.linspace(0, np.pi, n_samples - half)
    exp_flow = -amplitude * 0.7 * np.sin(exp_t)

    flow_values = np.concatenate([insp_flow, exp_flow])

    return timestamps, flow_values


def generate_multi_peak_breath(
    duration: float = 4.0,
    amplitude: float = 30.0,
    sample_rate: float = 25.0,
    peak_count: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a breath with multiple peaks during inspiration.

    Simulates flow limitation patterns like double-peak or vibration.

    Args:
        duration: Breath duration in seconds
        amplitude: Peak flow amplitude in L/min
        sample_rate: Sample rate in Hz
        peak_count: Number of peaks during inspiration

    Returns:
        Tuple of (timestamps, flow_values)
    """
    n_samples = int(duration * sample_rate)
    timestamps = np.linspace(0, duration, n_samples)

    half = n_samples // 2

    insp_t = np.linspace(0, np.pi, half)
    base_insp = amplitude * np.sin(insp_t)

    oscillation = 0.3 * amplitude * np.sin(peak_count * np.pi * insp_t / np.pi)
    insp_flow = base_insp + oscillation

    exp_t = np.linspace(0, np.pi, n_samples - half)
    exp_flow = -amplitude * 0.7 * np.sin(exp_t)

    flow_values = np.concatenate([insp_flow, exp_flow])

    return timestamps, flow_values


def create_session(
    num_breaths: int = 30,
    avg_duration: float = 4.0,
    duration_variability: float = 0.5,
    avg_amplitude: float = 30.0,
    amplitude_variability: float = 5.0,
    sample_rate: float = 25.0,
    breath_type: str = "sinusoidal",
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a complete session with multiple breaths.

    Args:
        num_breaths: Number of breaths to generate
        avg_duration: Average breath duration in seconds
        duration_variability: Std dev of duration variation
        avg_amplitude: Average breath amplitude in L/min
        amplitude_variability: Std dev of amplitude variation
        sample_rate: Sample rate in Hz
        breath_type: Type of breath ("sinusoidal", "flattened", "multi_peak")

    Returns:
        Tuple of (timestamps, flow_values) for entire session
    """
    all_timestamps = []
    all_flow_values = []
    current_time = 0.0

    for _i in range(num_breaths):
        duration = np.random.normal(avg_duration, duration_variability)
        duration = max(1.0, duration)  # Minimum 1 second

        amplitude = np.random.normal(avg_amplitude, amplitude_variability)
        amplitude = max(10.0, amplitude)  # Minimum 10 L/min

        if breath_type == "sinusoidal":
            t, flow = generate_sinusoidal_breath(duration, amplitude, sample_rate)
        elif breath_type == "flattened":
            t, flow = generate_flattened_breath(duration, amplitude, sample_rate)
        elif breath_type == "multi_peak":
            t, flow = generate_multi_peak_breath(duration, amplitude, sample_rate)
        else:
            raise ValueError(f"Unknown breath type: {breath_type}")

        t = t + current_time
        current_time = t[-1]

        all_timestamps.extend(t)
        all_flow_values.extend(flow)

    return np.array(all_timestamps), np.array(all_flow_values)
