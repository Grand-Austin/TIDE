"""MSS Isherwood (1972) horizontal wind loads, SI and FSD axes.

Ported from MSS ce0b40ed101411fc4eebd064b35c5a96916106ad, copyright Thor I.
Fossen, MIT. See data/vessels/s175/MSS-LICENSE.txt. The S175 example geometry
below is not an experimentally calibrated description of the loading condition.
"""

from dataclasses import dataclass
import math

import numpy as np

from .dynamics import rotation


@dataclass(frozen=True)
class WindParameters:
    # shortcut: S175 demo windage; replace after loading-specific aerodynamic calibration.
    loa_m: float = 175.0
    breadth_m: float = 25.4
    lateral_area_m2: float = 2000.0
    frontal_area_m2: float = 100.0
    superstructure_area_m2: float = 200.0
    perimeter_m: float = 130.0
    centroid_from_bow_m: float = 30.0
    mast_groups: int = 0
    air_density_kg_m3: float = 1.224

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if name in ("mast_groups", "superstructure_area_m2", "centroid_from_bow_m"):
                if value < 0:
                    raise ValueError(f"{name} must be nonnegative")
            elif value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.superstructure_area_m2 > self.lateral_area_m2:
            raise ValueError("Superstructure area must not exceed lateral area")
        if self.centroid_from_bow_m > self.loa_m:
            raise ValueError("Windage centroid must lie within the length")
        if int(self.mast_groups) != self.mast_groups:
            raise ValueError("mast_groups must be an integer")


_CX = np.array([
    [0.0, 2.152, -5.0, 0.243, -0.164, 0.0, 0.0, 0.0],
    [10.0, 1.714, -3.33, 0.145, -0.121, 0.0, 0.0, 0.0],
    [20.0, 1.818, -3.97, 0.211, -0.143, 0.0, 0.0, 0.033],
    [30.0, 1.965, -4.81, 0.243, -0.154, 0.0, 0.0, 0.041],
    [40.0, 2.333, -5.99, 0.247, -0.19, 0.0, 0.0, 0.042],
    [50.0, 1.726, -6.54, 0.189, -0.173, 0.348, 0.0, 0.048],
    [60.0, 0.913, -4.68, 0.0, -0.104, 0.482, 0.0, 0.052],
    [70.0, 0.457, -2.88, 0.0, -0.068, 0.346, 0.0, 0.043],
    [80.0, 0.341, -0.91, 0.0, -0.031, 0.0, 0.0, 0.032],
    [90.0, 0.355, 0.0, 0.0, 0.0, -0.247, 0.0, 0.018],
    [100.0, 0.601, 0.0, 0.0, 0.0, -0.372, 0.0, -0.02],
    [110.0, 0.651, 1.29, 0.0, 0.0, -0.582, 0.0, -0.031],
    [120.0, 0.564, 2.54, 0.0, 0.0, -0.748, 0.0, -0.024],
    [130.0, -0.142, 3.58, 0.0, 0.047, -0.7, 0.0, -0.028],
    [140.0, -0.677, 3.64, 0.0, 0.069, -0.529, 0.0, -0.032],
    [150.0, -0.723, 3.14, 0.0, 0.064, -0.475, 0.0, -0.032],
    [160.0, -2.148, 2.56, 0.0, 0.081, 0.0, 1.27, -0.027],
    [170.0, -2.707, 3.97, -0.175, 0.126, 0.0, 1.81, 0.0],
    [180.0, -2.529, 3.76, -0.174, 0.128, 0.0, 1.55, 0.0],
], dtype=float)

_CY = np.array([
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [10.0, 0.096, 0.22, 0.0, 0.0, 0.0, 0.0, 0.0],
    [20.0, 0.176, 0.71, 0.0, 0.0, 0.0, 0.0, 0.0],
    [30.0, 0.225, 1.38, 0.0, 0.023, 0.0, -0.29, 0.0],
    [40.0, 0.329, 1.82, 0.0, 0.043, 0.0, -0.59, 0.0],
    [50.0, 1.164, 1.26, 0.121, 0.0, -0.242, -0.95, 0.0],
    [60.0, 1.163, 0.96, 0.101, 0.0, -0.177, -0.88, 0.0],
    [70.0, 0.916, 0.53, 0.069, 0.0, 0.0, -0.65, 0.0],
    [80.0, 0.844, 0.55, 0.082, 0.0, 0.0, -0.54, 0.0],
    [90.0, 0.889, 0.0, 0.138, 0.0, 0.0, -0.66, 0.0],
    [100.0, 0.799, 0.0, 0.155, 0.0, 0.0, -0.55, 0.0],
    [110.0, 0.797, 0.0, 0.151, 0.0, 0.0, -0.55, 0.0],
    [120.0, 0.996, 0.0, 0.184, 0.0, -0.212, -0.66, 0.34],
    [130.0, 1.014, 0.0, 0.191, 0.0, -0.28, -0.69, 0.44],
    [140.0, 0.784, 0.0, 0.166, 0.0, -0.209, -0.53, 0.38],
    [150.0, 0.536, 0.0, 0.176, -0.029, -0.163, 0.0, 0.27],
    [160.0, 0.251, 0.0, 0.106, -0.022, 0.0, 0.0, 0.0],
    [170.0, 0.125, 0.0, 0.046, -0.012, 0.0, 0.0, 0.0],
    [180.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
], dtype=float)

_CN = np.array([
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [10.0, 0.0596, 0.061, 0.0, 0.0, 0.0, -0.074],
    [20.0, 0.1106, 0.204, 0.0, 0.0, 0.0, -0.17],
    [30.0, 0.2258, 0.245, 0.0, 0.0, 0.0, -0.38],
    [40.0, 0.2017, 0.457, 0.0, 0.0067, 0.0, -0.472],
    [50.0, 0.1759, 0.573, 0.0, 0.0118, 0.0, -0.523],
    [60.0, 0.1925, 0.48, 0.0, 0.0115, 0.0, -0.546],
    [70.0, 0.2133, 0.315, 0.0, 0.0081, 0.0, -0.526],
    [80.0, 0.1827, 0.254, 0.0, 0.0053, 0.0, -0.443],
    [90.0, 0.2627, 0.0, 0.0, 0.0, 0.0, -0.508],
    [100.0, 0.2102, 0.0, -0.0195, 0.0, 0.0335, -0.492],
    [110.0, 0.1567, 0.0, -0.0258, 0.0, 0.0497, -0.457],
    [120.0, 0.0801, 0.0, -0.0311, 0.0, 0.074, -0.396],
    [130.0, -0.0189, 0.0, -0.0488, 0.0101, 0.1128, -0.42],
    [140.0, 0.0256, 0.0, -0.0422, 0.01, 0.0889, -0.463],
    [150.0, 0.0552, 0.0, -0.0381, 0.0109, 0.0689, -0.476],
    [160.0, 0.0881, 0.0, -0.0306, 0.0091, 0.0366, -0.415],
    [170.0, 0.0851, 0.0, -0.0122, 0.0025, 0.0, -0.22],
    [180.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
], dtype=float)


def isherwood72(gamma_rad, relative_speed_mps, parameters=WindParameters()):
    """Return horizontal [X,Y,N]; positive gamma produces starboard side force.

    Angles are normalized before applying lateral signs. This corrects the
    original function's inconsistent signs for equivalent angles outside +/-pi.
    """
    if not math.isfinite(gamma_rad) or not math.isfinite(relative_speed_mps) or relative_speed_mps < 0:
        raise ValueError("Wind angle and nonnegative speed must be finite")
    gamma = (gamma_rad + math.pi) % (2*math.pi) - math.pi
    angle = math.degrees(abs(gamma))
    a, b, c = (np.array([np.interp(angle, table[:, 0], table[:, i])
                        for i in range(1, table.shape[1])]) for table in (_CX, _CY, _CN))
    p = parameters
    geometry = np.array([1, 2*p.lateral_area_m2/p.loa_m**2,
                         2*p.frontal_area_m2/p.breadth_m**2, p.loa_m/p.breadth_m,
                         p.perimeter_m/p.loa_m, p.centroid_from_bow_m/p.loa_m])
    cx = -(a[:6] @ geometry + a[6]*p.mast_groups)
    cy = b[:6] @ geometry + b[6]*p.superstructure_area_m2/p.lateral_area_m2
    cn = c @ geometry
    dynamic_pressure = .5*p.air_density_kg_m3*relative_speed_mps**2
    return dynamic_pressure * np.array([cx*p.frontal_area_m2,
                                       cy*p.lateral_area_m2*np.sign(gamma),
                                       cn*p.lateral_area_m2*p.loa_m*np.sign(gamma)])


def wind_load(x, sea, parameters=WindParameters()):
    """Return [X,Y,0,0,0,N] from true NE wind minus ground vessel velocity.

    The empirical horizontal coefficients use yaw only. Wind-induced roll is
    not prescribed without a matching centre-of-pressure/roll model.
    """
    x = np.asarray(x, dtype=float)
    if x.shape != (12,) or not np.all(np.isfinite(x)):
        raise ValueError("State must be a finite 12-vector")
    wind = np.array([sea.wind_north_mps, sea.wind_east_mps], dtype=float)
    if not np.all(np.isfinite(wind)):
        raise ValueError("Wind must be a finite north/east vector")
    relative_ne = wind - (rotation(x[9:12]) @ x[:3])[:2]
    psi = x[11]
    body_x = math.cos(psi)*relative_ne[0] + math.sin(psi)*relative_ne[1]
    body_y = -math.sin(psi)*relative_ne[0] + math.cos(psi)*relative_ne[1]
    gamma = math.atan2(body_y, -body_x)
    longitudinal, lateral, yaw = isherwood72(gamma, float(np.linalg.norm(relative_ne)), parameters)
    return np.array([longitudinal, lateral, 0., 0., 0., yaw])
