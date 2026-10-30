function matched_maneuvering(mss_root, input_path, output_path)
% Recover the unique Son--Nomoto hull force from the pinned MSS container.m.
%
% The original model writes the four coupled horizontal equations as
%
%   M4 * [a_u; a_v; a_p; a_r] = [X; Y; K; N]
%
% with a_u = u_dot*L/U^2, a_v = v_dot*L/U^2,
%      a_p = p_dot*L^2/U^2, a_r = r_dot*L^2/U^2.
%
% container.m returns xdot = [u v r x y psi p phi delta n]'.  This script
% executes container.m, reconstructs the total normalized generalized force
% through the known original 4-DOF coupled mass matrix, and then removes once
% the source actuator, source Coriolis, and source roll-restoring terms.  The
% remaining force is the unique pure hull force.  The pure-force equations are
% also written explicitly below.  Both paths must agree; disagreement aborts
% the fixture.  No Python output is used as an expected value.
%
% The force section of container.m uses n = RPM/60 (rev/s), because the source
% re-dimensionalizes n before computing J, KT, FN and T.  The source normalizes
% force by 0.5*rho*L^2*U^2 and moment by 0.5*rho*L^3*U^2.

addpath(genpath(mss_root));
p = load(input_path);

L = 175.0;
rho = 1025.0;
g = 9.81;
nabla = 21222;
GM_source = 0.3 / L;
mutation_coefficient = 'Xvv';
mutation_factor = 1.01;

M4 = source_mass_matrix();
c = source_coefficients();

states = p.derivative_states;
orders = p.derivative_orders;
ncase = size(states, 1);

derivatives = zeros(ncase, 10);
total_norm = zeros(ncase, 4);
hull_force_norm = zeros(ncase, 4);
hull_force_si = zeros(ncase, 6);
explicit_hull_norm = zeros(ncase, 4);
explicit_hull_si = zeros(ncase, 6);
actuator_norm = zeros(ncase, 4);
coriolis_norm = zeros(ncase, 4);
restoring_norm = zeros(ncase, 4);
reconstruction_residual = zeros(ncase, 4);
hull_only_derivative = zeros(ncase, 10);
mutated_hull_norm = zeros(ncase, 4);
mutated_hull_si = zeros(ncase, 6);

for k = 1:ncase
    x = states(k, :)';
    ui = orders(k, :)';
    xdot = container(x, ui);

    u = x(1); v = x(2); r = x(3);
    p_rate = x(7); phi = x(8); delta = x(9); rpm = x(10);
    U = sqrt(u^2 + v^2);
    if U <= 0
        error('maneuvering oracle requires positive speed');
    end

    % Reconstruct the total normalized force from the original derivative.
    a = [xdot(1) * L / U^2
         xdot(2) * L / U^2
         xdot(7) * L^2 / U^2
         xdot(3) * L^2 / U^2];
    total = M4 * a;

    % Independent pure-force, actuator, Coriolis and restoring equations.
    hull = source_hull(u, v, p_rate, r, phi, L, false);
    mutated = source_hull(u, v, p_rate, r, phi, L, true);
    actuator = source_actuator(u, v, p_rate, r, phi, delta, rpm, L, rho);
    coriolis = source_coriolis(u, v, p_rate, r, phi, L);
    restoring = source_restoring(u, v, phi, L, rho);

    recovered = total - actuator - coriolis - restoring;
    residual = recovered - hull;

    force_scale = 0.5 * rho * L^2 * U^2;
    moment_scale = force_scale * L;

    derivatives(k, :) = xdot';
    total_norm(k, :) = total';
    hull_force_norm(k, :) = recovered';
    hull_force_si(k, :) = [recovered(1) * force_scale
                           recovered(2) * force_scale
                           0
                           recovered(3) * moment_scale
                           0
                           recovered(4) * moment_scale]';
    explicit_hull_norm(k, :) = hull';
    explicit_hull_si(k, :) = [hull(1) * force_scale
                              hull(2) * force_scale
                              0
                              hull(3) * moment_scale
                              0
                              hull(4) * moment_scale]';
    actuator_norm(k, :) = actuator';
    coriolis_norm(k, :) = coriolis';
    restoring_norm(k, :) = restoring';
    reconstruction_residual(k, :) = residual';
    hull_only_derivative(k, :) = hull_only_derivative_from_state(x, M4, L)';
    mutated_hull_norm(k, :) = mutated';
    mutated_hull_si(k, :) = [mutated(1) * force_scale
                             mutated(2) * force_scale
                             0
                             mutated(3) * moment_scale
                             0
                             mutated(4) * moment_scale]';
end

% Short pure-hull-force trajectories.  The source mass matrix propagates the
% hull force only; actuator, Coriolis, restoring, environment and servo terms
% are absent by construction.
short_x0 = p.short_x0;
short_h = p.short_h;
short_steps = p.short_steps;
short = zeros(size(short_x0, 1), short_steps + 1, 10);
for cindex = 1:size(short_x0, 1)
    x = short_x0(cindex, :)';
    short(cindex, 1, :) = x';
    for k = 1:short_steps
        x = rk4_hull(x, short_h, M4, L);
        short(cindex, k + 1, :) = x';
    end
end

% The 35-degree variant is an explicit force-algebra check.  The original
% container.m command servo saturates at 10 degrees.  The actual rudder state
% can still be 35 degrees, and the force equations use that actual angle.
rv = p.rudder_variant_state(:)';
rv_u = rv(1); rv_v = rv(2); rv_r = rv(3);
rv_p = rv(7); rv_phi = rv(8); rv_rpm = rv(10);
rv_U = sqrt(rv_u^2 + rv_v^2);
rv_force_scale = 0.5 * rho * L^2 * rv_U^2;
rv_moment_scale = rv_force_scale * L;
rudder_original_command_limit_deg = 10.0;
rudder_variant_deg = 35.0;
rudder_variant_10deg_norm = source_actuator(rv_u, rv_v, rv_p, rv_r, ...
    rv_phi, 10 * pi / 180, rv_rpm, L, rho);
rudder_variant_35deg_norm = source_actuator(rv_u, rv_v, rv_p, rv_r, ...
    rv_phi, 35 * pi / 180, rv_rpm, L, rho);
rudder_variant_10deg_si = [rudder_variant_10deg_norm(1) * rv_force_scale
                           rudder_variant_10deg_norm(2) * rv_force_scale
                           0
                           rudder_variant_10deg_norm(3) * rv_moment_scale
                           0
                           rudder_variant_10deg_norm(4) * rv_moment_scale];
rudder_variant_35deg_si = [rudder_variant_35deg_norm(1) * rv_force_scale
                           rudder_variant_35deg_norm(2) * rv_force_scale
                           0
                           rudder_variant_35deg_norm(3) * rv_moment_scale
                           0
                           rudder_variant_35deg_norm(4) * rv_moment_scale];

reconstruction_max_abs = max(abs(reconstruction_residual(:)));
explicit_match_max_abs = max(max(abs(hull_force_norm - explicit_hull_norm)));
mutation_detection_max_abs = max(max(abs(mutated_hull_norm - hull_force_norm)));
mass_matrix_normalized = M4;
length_m = L;
density = rho;
rudder_variant_state = p.rudder_variant_state;

save('-v7', output_path, 'derivatives', 'total_norm', 'hull_force_norm', ...
    'hull_force_si', 'explicit_hull_norm', 'explicit_hull_si', ...
    'actuator_norm', 'coriolis_norm', 'restoring_norm', ...
    'reconstruction_residual', 'reconstruction_max_abs', ...
    'explicit_match_max_abs', 'mutation_detection_max_abs', ...
    'hull_only_derivative', 'short', 'short_x0', 'short_h', 'short_steps', ...
    'mutated_hull_norm', 'mutated_hull_si', 'mutation_coefficient', ...
    'mutation_factor', 'mass_matrix_normalized', 'length_m', 'density', ...
    'rudder_variant_state', 'rudder_variant_10deg_norm', ...
    'rudder_variant_35deg_norm', 'rudder_variant_10deg_si', ...
    'rudder_variant_35deg_si', 'rudder_original_command_limit_deg', ...
    'rudder_variant_deg');
end

function c = source_coefficients()
% Coefficients exactly as written in pinned CRAFT/SHIP/models/container.m.
c.m = 0.00792;    c.mx = 0.000238;  c.my = 0.007049;
c.Ix = 0.0000176; c.alphay = 0.05;  c.lx = 0.0313;
c.ly = 0.0313;    c.Iz = 0.000456;
c.Jx = 0.0000034; c.Jz = 0.000419;

c.Xuu = -0.0004226; c.Xvr = -0.00311; c.Xrr = 0.00020;
c.Xphiphi = -0.00020; c.Xvv = -0.00386;

c.Kv = 0.0003026; c.Kr = -0.000063; c.Kp = -0.0000075;
c.Kphi = -0.000021; c.Kvvv = 0.002843; c.Krrr = -0.0000462;
c.Kvvr = -0.000588; c.Kvrr = 0.0010565; c.Kvvphi = -0.0012012;
c.Kvphiphi = -0.0000793; c.Krrphi = -0.000243; c.Krphiphi = 0.00003569;

c.Yv = -0.0116; c.Yr = 0.00242; c.Yp = 0;
c.Yphi = -0.000063; c.Yvvv = -0.109; c.Yrrr = 0.00177;
c.Yvvr = 0.0214; c.Yvrr = -0.0405; c.Yvvphi = 0.04605;
c.Yvphiphi = 0.00304; c.Yrrphi = 0.009325; c.Yrphiphi = -0.001368;

c.Nv = -0.0038545; c.Nr = -0.00222; c.Np = 0.000213;
c.Nphi = -0.0001424; c.Nvvv = 0.001492; c.Nrrr = -0.00229;
c.Nvvr = -0.0424; c.Nvrr = 0.00156; c.Nvvphi = -0.019058;
c.Nvphiphi = -0.0053766; c.Nrrphi = -0.0038592; c.Nrphiphi = 0.0024195;

c.kk = 0.631; c.epsilon = 0.921; c.xR = -0.5;
c.wp = 0.184; c.tau = 1.09; c.xp = -0.526;
c.cpv = 0.0; c.cpr = 0.0; c.ga = 0.088;
c.cRr = -0.156; c.cRrrr = -0.275; c.cRrrv = 1.96;
c.cRX = 0.71; c.aH = 0.237; c.zR = 0.033; c.xH = -0.48;
c.Delta = 1.8219; c.D = 6.533; c.AR = 33.0376; c.t = 0.175;
end

function M4 = source_mass_matrix()
% Original 4-DOF coupled mass matrix in normalized-force convention.
c = source_coefficients();
m11 = c.m + c.mx;
m22 = c.m + c.my;
m32 = -c.my * c.ly;
m42 = c.my * c.alphay;
m33 = c.Ix + c.Jx;
m44 = c.Iz + c.Jz;
M4 = [m11, 0,   0,   0
      0,   m22, m32, m42
      0,   m32, m33, 0
      0,   m42, 0,   m44];
end

function h = source_hull(u, v, p_rate, r, phi, L, mutate)
% Unique rudder-free Son--Nomoto hull force in normalized [X Y K N] form.
if nargin < 7
    mutate = false;
end
c = source_coefficients();
if mutate
    c.Xvv = c.Xvv * 1.01;
end
U = sqrt(u^2 + v^2);
un = u / U; vn = v / U;
pn = p_rate * L / U; rn = r * L / U;

Xh = c.Xuu * un^2 + c.Xvr * vn * rn + c.Xvv * vn^2 + ...
     c.Xrr * rn^2 + c.Xphiphi * phi^2;
Yh = c.Yv * vn + c.Yr * rn + c.Yp * pn + c.Yphi * phi + ...
     c.Yvvv * vn^3 + c.Yrrr * rn^3 + c.Yvvr * vn^2 * rn + ...
     c.Yvrr * vn * rn^2 + c.Yvvphi * vn^2 * phi + ...
     c.Yvphiphi * vn * phi^2 + c.Yrrphi * rn^2 * phi + ...
     c.Yrphiphi * rn * phi^2;
Kh = c.Kv * vn + c.Kr * rn + c.Kp * pn + c.Kphi * phi + ...
     c.Kvvv * vn^3 + c.Krrr * rn^3 + c.Kvvr * vn^2 * rn + ...
     c.Kvrr * vn * rn^2 + c.Kvvphi * vn^2 * phi + ...
     c.Kvphiphi * vn * phi^2 + c.Krrphi * rn^2 * phi + ...
     c.Krphiphi * rn * phi^2;
Nh = c.Nv * vn + c.Nr * rn + c.Np * pn + c.Nphi * phi + ...
     c.Nvvv * vn^3 + c.Nrrr * rn^3 + c.Nvvr * vn^2 * rn + ...
     c.Nvrr * vn * rn^2 + c.Nvvphi * vn^2 * phi + ...
     c.Nvphiphi * vn * phi^2 + c.Nrrphi * rn^2 * phi + ...
     c.Nrphiphi * rn * phi^2;
h = [Xh; Yh; Kh; Nh];
end

function a = source_actuator(u, v, p_rate, r, phi, delta, rpm, L, rho)
% Source propeller and rudder generalized force in normalized [X Y K N] form.
% The source uses n = RPM/60 in this force section.  The actual rudder angle
% delta is used; the 10-degree command limit is a separate servo constraint.
c = source_coefficients();
U = sqrt(u^2 + v^2);
un = u / U; vn = v / U; rn = r * L / U;
n = rpm / 60;

vR = c.ga * vn + c.cRr * rn + c.cRrrr * rn^3 + c.cRrrv * rn^2 * vn;
uP = un * ((1 - c.wp) + c.tau * ((vn + c.xp * rn)^2 + ...
     c.cpv * vn + c.cpr * rn));
J = uP * U / (n * c.D);
KT = 0.527 - 0.455 * J;
uR = uP * c.epsilon * sqrt(1 + 8 * c.kk * KT / (pi * J^2));
alphaR = delta + atan(vR / uR);
FN = -((6.13 * c.Delta) / (c.Delta + 2.25)) * (c.AR / L^2) * ...
     (uR^2 + vR^2) * sin(alphaR);
T = 2 * rho * c.D^4 / (U^2 * L^2 * rho) * KT * n * abs(n);

Xa = (1 - c.t) * T + c.cRX * FN * sin(delta);
Ya = (1 + c.aH) * FN * cos(delta);
Ka = -(1 + c.aH) * c.zR * FN * cos(delta);
Na = (c.xR + c.aH * c.xH) * FN * cos(delta);
a = [Xa; Ya; Ka; Na];
end

function a = source_coriolis(u, v, p_rate, r, phi, L)
% Source rigid/added-mass Coriolis terms in normalized [X Y K N] form.
c = source_coefficients();
U = sqrt(u^2 + v^2);
un = u / U; vn = v / U; rn = r * L / U;
Xc = (c.m + c.my) * vn * rn;
Yc = -(c.m + c.mx) * un * rn;
Kc = c.mx * c.lx * un * rn;
Nc = 0;
a = [Xc; Yc; Kc; Nc];
end

function a = source_restoring(u, v, phi, L, rho)
% Original source roll restoring -W*GM*phi in normalized K only.
c = source_coefficients();
U = sqrt(u^2 + v^2);
g = 9.81;
nabla = 21222;
GM = 0.3 / L;
W = rho * g * nabla / (rho * L^2 * U^2 / 2);
a = [0; 0; -W * GM * phi; 0];
end

function xd = hull_only_derivative_from_state(x, M4, L)
% Propagate the pure hull force through the original coupled mass matrix.
U = sqrt(x(1)^2 + x(2)^2);
hull = source_hull(x(1), x(2), x(7), x(3), x(8), L, false);
a = M4 \ hull;
xd = zeros(10, 1);
xd(1) = a(1) * U^2 / L;
xd(2) = a(2) * U^2 / L;
xd(3) = a(4) * U^2 / L^2;
xd(6) = cos(x(8)) * x(3);
xd(7) = a(3) * U^2 / L^2;
xd(8) = x(7);
end

function x = rk4_hull(x, h, M4, L)
k1 = hull_only_derivative_from_state(x, M4, L);
k2 = hull_only_derivative_from_state(x + h * k1 / 2, M4, L);
k3 = hull_only_derivative_from_state(x + h * k2 / 2, M4, L);
k4 = hull_only_derivative_from_state(x + h * k3, M4, L);
x = x + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6;
end
