function s175_reference(mss_root, output_path)
% Optional headless reference generator. Requires pinned MSS + Octave/MATLAB.
% Usage: s175_reference('data/raw/mss', 'data/runs/s175-reference.mat')
% This file has NOT been used to claim MATLAB/Octave parity without execution.
addpath(genpath(mss_root));
data = load(fullfile(mss_root, 'HYDRO/vessels_shipx/s175/s175.mat'));
vessel = data.vessel;
% Match the runtime's documented zero-speed, fixed-frequency reduction.
vessel.A = vessel.A(:,:,:,1);
vessel.B = vessel.B(:,:,:,1);
vessel.C = vessel.C(:,:,:,1);
% Derive feasible damping times from the source radiation damping. An arbitrary
% 120-second total decay time would require negative extra damping for S175.
freqs = vessel.freqs(:); w = linspace(min(freqs), max(freqs), 100)';
S = w.^(-5).*exp(-0.74./w.^4); S = S / trapz(w,S);
Aeq = zeros(6); Beq = zeros(6);
for i=1:6
    for j=1:6
        Aeq(i,j) = trapz(w, interp1(freqs,squeeze(vessel.A(i,j,:)),w,'pchip').*S);
        Beq(i,j) = trapz(w, interp1(freqs,squeeze(vessel.B(i,j,:)),w,'pchip').*S);
    end
end
% Pass this value explicitly; no GUI or other vessel's preset is used.
idx = [1 2 6]; M0 = vessel.MRB + Aeq;
T126 = min([120 120 120], 0.95 * diag(M0(idx,idx))' ./ diag(Beq(idx,idx))');
vessel = computeManeuveringModel(vessel, 1.0, T126, [0 0.1 0], 0);
vessel.kappa_4 = 0; vessel.kappa_5 = 0; vessel.kappa_6 = 0;
M = vessel.M; MA = vessel.MA; D = vessel.D; G = vessel.G;
x = [2; 0.1; 0; 0.01; 0; 0.002; 0; 0; 0; 0.02; 0.01; 0.5];
tau = [120000; 0; 0; 0; 0; 1000000];
current_ne = [0.3; 0.2];
Vc = norm(current_ne); betaVc = atan2(current_ne(2), current_ne(1));
xdot = hydroVessel(x, tau, vessel, Vc, betaVc);
h = 0.05; trajectory = zeros(201,12); trajectory(1,:) = x';
for i = 1:200
    trajectory(i+1,:) = rk4(@hydroVessel, h, trajectory(i,:)', tau, vessel, Vc, betaVc)';
end
% Fixed wave components make the force fixture independent of RNG libraries.
waveModel.raoType = 'force'; waveModel.g = vessel.main.g;
waveModel.Omega = [0.6; 0.9; 1.2]; waveModel.mu = [-0.2; 0.2];
waveModel.Amp = [0.1 0.1; 0.2 0.2; 0.1 0.1];
waveModel.S_M = waveModel.Amp.^2 / 2;
waveModel.randomPhases = [0.1 0.2; 0.3 0.4; 0.5 0.6];
waveModel.componentPhase = waveModel.randomPhases;
waveModel.phaseTime = []; waveModel.encounterRate = [];
waveModel.raoAngles = [vessel.headings(:); 2*pi];
for dof=1:6
    coeff = vessel.forceRAO.amp{dof}(:,:,1) .* exp(1i*vessel.forceRAO.phase{dof}(:,:,1));
    real_part = interp1(vessel.forceRAO.w(:), real(coeff), waveModel.Omega, 'linear');
    imag_part = interp1(vessel.forceRAO.w(:), imag(coeff), waveModel.Omega, 'linear');
    waveModel.RAO_re{dof} = [real_part real_part(:,1)];
    waveModel.RAO_im{dof} = [imag_part imag_part(:,1)];
end
[wave_force, wave_elevation, waveModel] = waveForceRAO(0.1, waveModel, 2, 0.5, 2.0);
% S175 demo wind geometry; signed angles avoid the upstream wrapping ambiguity.
wind_angles = [-pi/2 0 pi/2 pi]; wind_forces = zeros(3,4);
for i=1:4
    wind_forces(:,i) = isherwood72(wind_angles(i),10,175,25.4,2000,100,200,130,30,0);
end
save('-v7', output_path, 'M', 'MA', 'D', 'G', 'T126', 'x', 'tau', 'current_ne', 'xdot', ...
     'h', 'trajectory', 'wave_force', 'wave_elevation', 'waveModel', 'wind_angles', 'wind_forces');
end
