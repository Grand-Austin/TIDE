function matched_two_timescale(mss_root, input_path, output_path)
% Independent Octave reference for the approved LF/WF two-time-scale model.
%
% LF: native four-DOF Son--Nomoto state [u,v,p,r,N,E,phi,psi] with the actual
%     S175 rigid mass/inertia and G(4,4) roll restoring, source pure hull
%     force, source convective vector, physical actuator loads and 35-degree
%     servo variant.
% WF: original pinned MSS hydroVessel.m on the 12-state perturbation
%     [uW,vW,wW,pW,qW,rW,xW,yW,zW,phiW,thetaW,psiW] with zero current and the
%     held first-order wave-force tape only.
% Coupled RK4: 8 LF + 12 WF + 2 servo states = 22.  External inputs are held
% for each step.  The composed 12-state pose and derivative use the root
% integration contract, not additive Euler angles.
%
% This file is an independent physical oracle.  It does not use Python forces
% as expected values.  It does not edit the pinned MSS source.

addpath(genpath(mss_root));
p = load(input_path);

vessel.main = p.vessel_main;
vessel.MA = p.vessel_MA;
vessel.Minv = p.vessel_Minv;
vessel.D = p.vessel_D;
vessel.G = p.vessel_G;
vessel.kappa_4 = p.vessel_kappa(1);
vessel.kappa_5 = p.vessel_kappa(2);
vessel.kappa_6 = p.vessel_kappa(3);

L = p.lf_L;
rho = p.lf_rho;
m = p.lf_m;
ixx = p.lf_ixx;
izz = p.lf_izz;
Groll = p.lf_Groll;
M4 = lf_mass_octave(L, rho, m, ixx, izz);

ncase = p.ncase;
steps = p.steps;
h = p.dt;
lf_trajectory = zeros(ncase, steps + 1, 8);
wf_trajectory = zeros(ncase, steps + 1, 12);
servo_trajectory = zeros(ncase, steps + 1, 2);
total_x = zeros(ncase, steps + 1, 12);
total_dx = zeros(ncase, steps + 1, 12);
derivative_start = zeros(ncase, 22);
derivative_mid = zeros(ncase, 22);
derivative_end = zeros(ncase, 22);
derivative_state_indices = [1, floor(steps / 2) + 1, steps + 1];
derivative_input_indices = [1, floor(steps / 2) + 1, steps];

for c = 1:ncase
    lf = p.lf0(c, :)';
    wf = p.wf0(c, :)';
    rudder = p.rudder0(c);
    rpm = p.rpm0(c);
    z = [lf; wf; rudder; rpm];

    input = case_input(p, c, 1);
    d0 = coupled_derivative(z, input, vessel, M4, L, rho, m, Groll);
    [x0, ~] = compose_octave(lf, wf, zeros(8, 1), zeros(12, 1), input.current);
    total_x(c, 1, :) = x0';
    [~, dx0] = compose_octave(lf, wf, d0(1:8), d0(9:20), input.current);
    total_dx(c, 1, :) = dx0';
    derivative_start(c, :) = d0';

    for k = 1:steps
        input = case_input(p, c, k);
        [z_next, ~, d_end] = rk4_step(z, h, input, vessel, M4, L, rho, m, Groll);
        z = z_next;
        lf = z(1:8);
        wf = z(9:20);
        lf_trajectory(c, k + 1, :) = lf';
        wf_trajectory(c, k + 1, :) = wf';
        servo_trajectory(c, k + 1, :) = z(21:22)';
        [x_end, ~] = compose_octave(lf, wf, zeros(8, 1), zeros(12, 1), input.current);
        total_x(c, k + 1, :) = x_end';
        [~, dx_end] = compose_octave(lf, wf, d_end(1:8), d_end(9:20), input.current);
        total_dx(c, k + 1, :) = dx_end';
    end

    lf_trajectory(c, 1, :) = p.lf0(c, :);
    wf_trajectory(c, 1, :) = p.wf0(c, :);
    servo_trajectory(c, 1, :) = [p.rudder0(c), p.rpm0(c)];

    input = case_input(p, c, 1);
    derivative_start(c, :) = coupled_derivative( ...
        [squeeze(lf_trajectory(c, 1, :)); squeeze(wf_trajectory(c, 1, :)); ...
         servo_trajectory(c, 1, 1); servo_trajectory(c, 1, 2)], ...
        input, vessel, M4, L, rho, m, Groll)';
    mid_index = derivative_state_indices(2);
    input = case_input(p, c, derivative_input_indices(2));
    derivative_mid(c, :) = coupled_derivative( ...
        [squeeze(lf_trajectory(c, mid_index, :)); ...
         squeeze(wf_trajectory(c, mid_index, :)); ...
         servo_trajectory(c, mid_index, 1); servo_trajectory(c, mid_index, 2)], ...
        input, vessel, M4, L, rho, m, Groll)';
    input = case_input(p, c, steps);
    derivative_end(c, :) = coupled_derivative( ...
        [squeeze(lf_trajectory(c, steps + 1, :)); ...
         squeeze(wf_trajectory(c, steps + 1, :)); ...
         servo_trajectory(c, steps + 1, 1); servo_trajectory(c, steps + 1, 2)], ...
        input, vessel, M4, L, rho, m, Groll)';
end

save('-v7', output_path, 'lf_trajectory', 'wf_trajectory', ...
    'servo_trajectory', 'total_x', 'total_dx', 'derivative_start', ...
    'derivative_mid', 'derivative_end', 'derivative_state_indices', ...
    'derivative_input_indices');
end

function input = case_input(p, case_index, step_index)
input.current = squeeze(p.current(case_index, step_index, :));
input.wind_force = squeeze(p.wind_force(case_index, step_index, :));
input.mean_surge = p.mean_surge(case_index, step_index);
input.wave_force = squeeze(p.wave_force(case_index, step_index, :));
input.rudder_order = p.rudder_order(case_index, step_index);
input.rpm_order = p.rpm_order(case_index, step_index);
end

function M4 = lf_mass_octave(L, rho, m, ixx, izz)
mass_scale = 0.5 * rho * L^3;
inertia_scale = 0.5 * rho * L^5;
mx = 0.000238 * mass_scale;
my = 0.007049 * mass_scale;
jx = 0.0000034 * inertia_scale;
jz = 0.000419 * inertia_scale;
lyL = 0.0313 * L;
alphaL = 0.05 * L;
M4 = [m + mx, 0, 0, 0
      0, m + my, -my * lyL, my * alphaL
      0, -my * lyL, ixx + jx, 0
      0, my * alphaL, 0, izz + jz];
end

function zdot = coupled_derivative(z, input, vessel, M4, L, rho, m, Groll)
lf = z(1:8);
wf = z(9:20);
rudder = z(21);
rpm = z(22);
velocity = [lf(1); lf(2); 0; lf(3); 0; lf(4)];
external = actuator_loads(lf(1), lf(2), lf(4), rudder, rpm, rho);
external = external + input.wind_force;
external(1) = external(1) + input.mean_surge;
lf_dot = lf_derivative_octave(lf, external, M4, L, rho, m, Groll, input.current);
wf_dot = hydroVessel(wf, input.wave_force, vessel, 0, 0);
servo = servo_rates_octave(rudder, rpm, input.rudder_order, input.rpm_order);
zdot = [lf_dot; wf_dot; servo];
end

function xd = lf_derivative_octave(lf, external, M4, L, rho, m, Groll, current)
u = lf(1); v = lf(2); p_rate = lf(3); r = lf(4);
phi = lf(7); psi = lf(8);
mass_scale = 0.5 * rho * L^3;
mx = 0.000238 * mass_scale;
my = 0.007049 * mass_scale;
lxL = 0.0313 * L;
hull = hull_force_si(u, v, p_rate, r, phi, L, rho);
force = external([1, 2, 4, 6]) + hull([1, 2, 4, 6]);
force(1) = force(1) + (m + my) * v * r;
force(2) = force(2) - (m + mx) * u * r;
force(3) = force(3) + mx * lxL * u * r - Groll * phi;
acceleration = M4 \ force;
cos_phi = cos(phi); sin_phi = sin(phi);
cos_psi = cos(psi); sin_psi = sin(psi);
xd = [acceleration(1)
      acceleration(2)
      acceleration(3)
      acceleration(4)
      cos_psi * u - sin_psi * cos_phi * v + current(1)
      sin_psi * u + cos_psi * cos_phi * v + current(2)
      p_rate
      cos_phi * r];
end

function force = hull_force_si(u, v, p_rate, r, phi, L, rho)
U = sqrt(u^2 + v^2);
if U <= 0
    force = zeros(6, 1);
    return;
end
un = u / U; vn = v / U;
pn = p_rate * L / U; rn = r * L / U;

Xuu = -0.0004226; Xvr = -0.00311; Xrr = 0.00020;
Xphiphi = -0.00020; Xvv = -0.00386;
Kv = 0.0003026; Kr = -0.000063; Kp = -0.0000075;
Kphi = -0.000021; Kvvv = 0.002843; Krrr = -0.0000462;
Kvvr = -0.000588; Kvrr = 0.0010565; Kvvphi = -0.0012012;
Kvphiphi = -0.0000793; Krrphi = -0.000243; Krphiphi = 0.00003569;
Yv = -0.0116; Yr = 0.00242; Yp = 0;
Yphi = -0.000063; Yvvv = -0.109; Yrrr = 0.00177;
Yvvr = 0.0214; Yvrr = -0.0405; Yvvphi = 0.04605;
Yvphiphi = 0.00304; Yrrphi = 0.009325; Yrphiphi = -0.001368;
Nv = -0.0038545; Nr = -0.00222; Np = 0.000213;
Nphi = -0.0001424; Nvvv = 0.001492; Nrrr = -0.00229;
Nvvr = -0.0424; Nvrr = 0.00156; Nvvphi = -0.019058;
Nvphiphi = -0.0053766; Nrrphi = -0.0038592; Nrphiphi = 0.0024195;

Xh = Xuu * un^2 + Xvr * vn * rn + Xvv * vn^2 + ...
     Xrr * rn^2 + Xphiphi * phi^2;
Yh = Yv * vn + Yr * rn + Yp * pn + Yphi * phi + ...
     Yvvv * vn^3 + Yrrr * rn^3 + Yvvr * vn^2 * rn + ...
     Yvrr * vn * rn^2 + Yvvphi * vn^2 * phi + ...
     Yvphiphi * vn * phi^2 + Yrrphi * rn^2 * phi + ...
     Yrphiphi * rn * phi^2;
Kh = Kv * vn + Kr * rn + Kp * pn + Kphi * phi + ...
     Kvvv * vn^3 + Krrr * rn^3 + Kvvr * vn^2 * rn + ...
     Kvrr * vn * rn^2 + Kvvphi * vn^2 * phi + ...
     Kvphiphi * vn * phi^2 + Krrphi * rn^2 * phi + ...
     Krphiphi * rn * phi^2;
Nh = Nv * vn + Nr * rn + Np * pn + Nphi * phi + ...
     Nvvv * vn^3 + Nrrr * rn^3 + Nvvr * vn^2 * rn + ...
     Nvrr * vn * rn^2 + Nvvphi * vn^2 * phi + ...
     Nvphiphi * vn * phi^2 + Nrrphi * rn^2 * phi + ...
     Nrphiphi * rn * phi^2;

force_scale = 0.5 * rho * L^2 * U^2;
moment_scale = force_scale * L;
force = [Xh * force_scale
         Yh * force_scale
         0
         Kh * moment_scale
         0
         Nh * moment_scale];
end

function force = actuator_loads(u, v, r, rudder, rpm, rho)
% Same physical algebra as game.simulation.actuators.loads, in SI/FSD.
L = 175.0;
D = 6.533;
speed = sqrt(u^2 + v^2);
r_scaled = r * L;
propeller = u * (0.816 + 1.09 * ((v - 0.526 * r_scaled) / speed)^2);
lateral = 0.088 * v - 0.156 * r_scaled + ...
    (-0.275 * r_scaled^3 + 1.96 * r_scaled^2 * v) / speed^2;
n_d = rpm / 60 * D;
thrust = rho * D^2 * (0.527 * n_d^2 - 0.455 * propeller * n_d);
axial_squared = 0.921^2 * (propeller^2 + 8 * 0.631 / pi * ...
    (0.527 * n_d^2 - 0.455 * propeller * n_d));
axial = sqrt(axial_squared);
angle = rudder + atan2(lateral, axial);
normal = -0.5 * rho * (6.13 * 1.8219 / (1.8219 + 2.25)) * 33.0376 * ...
    (axial_squared + lateral^2) * sin(angle);
side = normal * cos(rudder);
force = [0.825 * thrust + 0.71 * normal * sin(rudder)
         1.237 * side
         0
         -1.237 * 0.033 * L * side
         0
         (-0.5 - 0.237 * 0.48) * L * side];
end

function rates = servo_rates_octave(rudder, rpm, rudder_order, rpm_order)
rudder_limit = 35 * pi / 180;
rate_limit = 5 * pi / 180;
rpm_limit = 160.0;
order = min(max(rudder_order, -rudder_limit), rudder_limit);
shaft_order = min(max(rpm_order, 0.0), rpm_limit);
rudder_rate = min(max(order - rudder, -rate_limit), rate_limit);
n = rpm / 60;
if n > 0.3
    Tm = 5.65 / n;
else
    Tm = 18.83;
end
rates = [rudder_rate; (shaft_order - rpm) / Tm];
end

function [z_next, k1, kend] = rk4_step(z, h, input, vessel, M4, L, rho, m, Groll)
k1 = coupled_derivative(z, input, vessel, M4, L, rho, m, Groll);
k2 = coupled_derivative(z + h * k1 / 2, input, vessel, M4, L, rho, m, Groll);
k3 = coupled_derivative(z + h * k2 / 2, input, vessel, M4, L, rho, m, Groll);
k4 = coupled_derivative(z + h * k3, input, vessel, M4, L, rho, m, Groll);
z_next = z + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6;
kend = coupled_derivative(z_next, input, vessel, M4, L, rho, m, Groll);
end

function [x, dx] = compose_octave(lf, wf, lf_dot, wf_dot, current)
u = lf(1); v = lf(2); p_rate = lf(3); r = lf(4);
north = lf(5); east = lf(6); phi = lf(7); psi = lf(8);
udot = lf_dot(1); vdot = lf_dot(2); pdot = lf_dot(3); rdot = lf_dot(4);
xi = wf(7:9);
omega_w = wf(4:6);
alpha_w = wf_dot(4:6);

R_L = rz(psi) * rx(phi);
R_W = rotation_zyx(wf(10:12));
R = R_L * R_W;

cos_phi = cos(phi); sin_phi = sin(phi);
heading_rate = cos_phi * r;
heading_accel = cos_phi * rdot - sin_phi * p_rate * r;

velocity_lf_n = rz(psi) * [u; v * cos_phi; 0];
velocity_lf_n(1) = velocity_lf_n(1) + current(1);
velocity_lf_n(2) = velocity_lf_n(2) + current(2);
acceleration_lf_n = rz(psi) * ([udot; vdot * cos_phi - v * sin_phi * p_rate; 0] ...
    + cross([0; 0; heading_rate], [u; v * cos_phi; 0]));
omega_lf = [p_rate; sin_phi * heading_rate; cos_phi * heading_rate];
alpha_lf = [pdot
            cos_phi * p_rate * heading_rate + sin_phi * heading_accel
            -sin_phi * p_rate * heading_rate + cos_phi * heading_accel];

xi_dot_l = R_W * wf(1:3);
xi_ddot_l = R_W * (wf_dot(1:3) + cross(omega_w, wf(1:3)));
velocity_n = velocity_lf_n + R_L * (xi_dot_l + cross(omega_lf, xi));
acceleration_n = acceleration_lf_n + R_L * (xi_ddot_l ...
    + 2 * cross(omega_lf, xi_dot_l) + cross(alpha_lf, xi) ...
    + cross(omega_lf, cross(omega_lf, xi)));

velocity_b = R' * velocity_n;
omega_b = R_W' * omega_lf + omega_w;
alpha_b = R_W' * alpha_lf + alpha_w + cross(R_W' * omega_lf, omega_w);
acceleration_b = R' * acceleration_n;
position = [north; east; 0] + R_L * xi;
angles = total_angles(R, psi);

x = [velocity_b; omega_b; position; angles];
dx = [acceleration_b - cross(omega_b, velocity_b)
      alpha_b
      velocity_n
      euler_rates(angles, omega_b)];
end

function R = rz(angle)
c = cos(angle); s = sin(angle);
R = [c, -s, 0; s, c, 0; 0, 0, 1];
end

function R = rx(angle)
c = cos(angle); s = sin(angle);
R = [1, 0, 0; 0, c, -s; 0, s, c];
end

function R = rotation_zyx(angles)
phi = angles(1); theta = angles(2); psi = angles(3);
cp = cos(phi); sp = sin(phi);
ct = cos(theta); st = sin(theta);
cy = cos(psi); sy = sin(psi);
R = [cy*ct, -sy*cp+cy*st*sp, sy*sp+cy*cp*st
     sy*ct, cy*cp+sp*st*sy, -cy*sp+st*sy*cp
     -st, ct*sp, ct*cp];
end

function angles = total_angles(R, lf_psi)
sin_theta = min(max(-R(3, 1), -1), 1);
theta = asin(sin_theta);
if abs(cos(theta)) < 1e-10
    error('Composed Euler angles are singular near pitch = +/-90 degrees');
end
phi = atan2(R(3, 2), R(3, 3));
psi_raw = atan2(R(2, 1), R(1, 1));
psi = lf_psi + wrap_angle(psi_raw - lf_psi);
angles = [phi; theta; psi];
end

function angle = wrap_angle(angle)
angle = mod(angle + pi, 2 * pi) - pi;
end

function rates = euler_rates(angles, omega)
phi = angles(1); theta = angles(2);
ct = cos(theta);
if abs(ct) < 1e-10
    error('Composed Euler kinematics are singular near pitch = +/-90 degrees');
end
sp = sin(phi); cp = cos(phi); tt = tan(theta);
T = [1, sp * tt, cp * tt
     0, cp, -sp
     0, sp / ct, cp / ct];
rates = T * omega;
end
