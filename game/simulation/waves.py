"""First-order total-sea forces and sourced diagonal mean surge drift.

Derived from MSS waveSpectrum / waveForceRAO / waveEncounterPhase, copyright
Thor I. Fossen, MIT. See data/vessels/s175/MSS-LICENSE.txt. Fixed quadrature and
water-relative vector encounter rates adapt the reference to changing fields.
Mean surge uses the pinned ShipX drift table's F=Q*a^2 convention.
"""

from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np
from scipy.interpolate import interp1d


@dataclass
class WaveState:
    phase: np.ndarray
    previous_rate: np.ndarray | None = None
    time_s: float | None = None


@dataclass(frozen=True)
class WaveResult:
    force: np.ndarray
    elevation: float
    state: WaveState
    retained_energy: float
    flags: tuple[str, ...]
    mean_surge_n: float = 0.0


def jonswap(omega, hs, peak, gamma=3.3):
    """MSS/DNV JONSWAP density in m² s, evaluated at positive rad/s."""
    omega = np.asarray(omega)
    sigma = np.where(omega < peak, 0.07, 0.09)
    enhancement = gamma ** np.exp(-((omega - peak) / (sigma * peak))**2 / 2)
    return ((1 - 0.287 * math.log(gamma)) * (5 / 16) * hs**2 * peak**4
            * omega**-5 * np.exp(-1.25 * (peak / omega)**4) * enhancement)


class WaveModel:
    def __init__(self, vessel, seed=1, n_frequencies=128, n_directions=12):
        if not isinstance(n_frequencies, int) or n_frequencies < 2 or not isinstance(n_directions, int) or n_directions < 1:
            raise ValueError("Positive quadrature counts are required (at least two frequencies)")
        self.g = float(vessel.main['g'])
        self.headings = np.append(vessel.headings, 2 * np.pi)
        # Relative peak width is nearly constant; log spacing resolves long-period seas
        # without moving components or resetting their phases when Tp changes.
        self.edges = np.geomspace(vessel.freqs[0], vessel.freqs[-1], n_frequencies + 1)
        self.omega = np.sqrt(self.edges[:-1] * self.edges[1:])
        self.dw = np.diff(self.edges)
        self.mu = (np.arange(n_directions) + 0.5) * np.pi / n_directions - np.pi / 2
        self.direction_weights = np.cos(self.mu)**2
        self.direction_weights /= self.direction_weights.sum()
        rao = interp1d(vessel.freqs, vessel.force_rao, axis=1, bounds_error=True)(self.omega)
        self.rao = np.concatenate([rao, rao[:, :, :1]], axis=2)
        self.drift_speeds = getattr(vessel, 'drift_speeds', None)
        self.surge_drift = None
        if self.drift_speeds is not None:
            drift = interp1d(vessel.drift_freqs, vessel.surge_drift, axis=0, bounds_error=True)(self.omega)
            self.surge_drift = np.concatenate([drift, drift[:, :1]], axis=1)
        self.initial_phases = np.random.default_rng(seed).uniform(0, 2*np.pi, (n_frequencies, n_directions))

    def initial_state(self):
        return WaveState(self.initial_phases.copy())

    @lru_cache(maxsize=128)
    def _spectrum(self, tp):
        peak = 2 * math.pi / tp
        if not self.edges[0] < peak < self.edges[-1]:
            raise ValueError("Wave peak is outside the source RAO frequency range")
        # A fixed frequency grid preserves each component's identity as Tp changes.
        density = jonswap(self.omega, 1, peak)
        broad = np.geomspace(peak/100, peak*100, 2048)
        band = np.linspace(self.edges[0], self.edges[-1], 1024)
        total_energy = np.trapezoid(jonswap(broad, 1, peak), broad)
        retained = np.trapezoid(jonswap(band, 1, peak), band) / total_energy
        energies = density * self.dw
        return density, float(np.clip(retained, 0, 1)), bool(energies.max() > 0.1*energies.sum())

    @lru_cache(maxsize=128)
    def _drift_coefficients(self, tp):
        density, _, _ = self._spectrum(tp)
        # MSS read_veres_WD: F_mean = Q * a^2; each random component has a^2=2*S*dw.
        return np.tensordot(2*density*self.dw, self.surge_drift, axes=(0, 0))

    def evaluate(self, state, time_s, sea, heading_rad, velocity_ne):
        velocity_ne = np.asarray(velocity_ne, dtype=float)
        if velocity_ne.shape != (2,) or not np.all(np.isfinite(velocity_ne)):
            raise ValueError("Wave encounter velocity must be a finite north/east vector")
        if not all(map(math.isfinite, (time_s, heading_rad, sea.hs, sea.wave_from_deg))) or time_s < 0 or sea.hs < 0:
            raise ValueError("Invalid wave inputs")
        if state.time_s is not None and time_s < state.time_s:
            raise ValueError("Wave time must not decrease")
        if state.phase.shape != self.initial_phases.shape or not np.all(np.isfinite(state.phase)):
            raise ValueError("Invalid wave phase state")
        propagation = math.radians(sea.wave_from_deg) + math.pi + self.mu
        relative = (propagation - heading_rad) % (2*np.pi)
        component_speed = velocity_ne[0]*np.cos(propagation) + velocity_ne[1]*np.sin(propagation)
        rate = self.omega[:, None] - (self.omega[:, None]**2 / self.g) * component_speed
        if state.time_s is None:
            phase = state.phase + time_s * rate
        else:
            phase = state.phase + (time_s - state.time_s) * (state.previous_rate + rate) / 2
        new_state = WaveState(phase, rate, time_s)
        if sea.hs == 0:
            return WaveResult(np.zeros(6), 0.0, new_state, 1.0, ())
        if not math.isfinite(sea.tp) or sea.tp <= 0:
            raise ValueError("Wave period must be finite and positive")
        density, retained, underresolved = self._spectrum(float(sea.tp))
        amplitude = np.sqrt(2 * density[:, None] * self.dw[:, None] * self.direction_weights) * sea.hs
        lower = np.searchsorted(self.headings, relative, side='right') - 1
        lower = np.minimum(lower, len(self.headings) - 2)
        weight = (relative - self.headings[lower]) / (self.headings[lower+1] - self.headings[lower])
        rao = self.rao[:, :, lower] * (1-weight) + self.rao[:, :, lower+1] * weight
        phasor = amplitude * np.exp(1j*phase)
        force = np.real(rao * phasor).sum(axis=(1, 2))
        flags = []
        mean_surge = 0.0
        if self.surge_drift is not None:
            coefficients = self._drift_coefficients(float(sea.tp))
            directional = coefficients[lower]*(1-weight[:, None]) + coefficients[lower+1]*weight[:, None]
            curve = np.sum(directional*self.direction_weights[:, None], axis=0)
            forward = float(velocity_ne @ [math.cos(heading_rad), math.sin(heading_rad)])
            if forward < self.drift_speeds[0] or forward > self.drift_speeds[-1]:
                flags.append('mean_drift_speed_clipped')
            mean_surge = float(np.interp(forward, self.drift_speeds, curve)*sea.hs**2)
        if retained < 0.95:
            flags.append('spectrum_truncated')
        if underresolved:
            flags.append('spectrum_underresolved')
        if not 1.25 <= 2*math.pi/sea.tp * math.sqrt(sea.hs) <= 1.75:
            flags.append('spectrum_assumption')
        return WaveResult(force, float(phasor.real.sum()), new_state, retained, tuple(flags), mean_surge)
