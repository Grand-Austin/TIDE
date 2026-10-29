function matched_dynamics(mss_root, input_path, output_path)
% Replay identical matrices, states, control forces and integration clock in MSS.
addpath(genpath(mss_root));
p = load(input_path);
vessel.main = p.main;
vessel.MA = p.MA; vessel.Minv = p.Minv; vessel.D = p.D; vessel.G = p.G;
vessel.kappa_4 = p.nonlinear_456(1);
vessel.kappa_5 = p.nonlinear_456(2);
vessel.kappa_6 = p.nonlinear_456(3);
derivatives = zeros(size(p.states));
for k = 1:size(p.states, 1)
    current = p.currents(k,:);
    derivatives(k,:) = hydroVessel(p.states(k,:)', p.forces(k,:)', vessel, ...
        norm(current), atan2(current(2),current(1)))';
end
trajectory = zeros(size(p.tau_history,1)+1,12);
trajectory(1,:) = p.x0(:)';
for k = 1:size(p.tau_history,1)
    current = p.current_history(k,:);
    trajectory(k+1,:) = rk4(@hydroVessel, p.h, trajectory(k,:)', ...
        p.tau_history(k,:)', vessel, norm(current), atan2(current(2),current(1)))';
end
% Separately check conversion, without changing matrices used by the replay.
raw = load(fullfile(mss_root,'HYDRO/vessels_shipx/s175/s175.mat'));
source = raw.vessel;
source.A = source.A(:,:,:,1); source.B = source.B(:,:,:,1); source.C = source.C(:,:,:,1);
source = computeManeuveringModel(source, 1, p.T126, [0 0.1 0], 0);
converted_M = source.M; converted_MA = source.MA;
converted_D = source.D; converted_G = source.G;
% Use the same wave components and phases; random seeds alone are not portable.
waveModel.raoType = 'force'; waveModel.g = p.main.g;
waveModel.Omega = p.omega(:); waveModel.mu = p.mu(:);
waveModel.Amp = p.wave_amplitude; waveModel.S_M = p.wave_amplitude.^2/2;
waveModel.randomPhases = p.wave_phases;
waveModel.raoAngles = p.rao_angles(:);
for dof = 1:6
    coefficients = squeeze(p.rao(dof,:,:));
    waveModel.RAO_re{dof} = real(coefficients);
    waveModel.RAO_im{dof} = imag(coefficients);
end
[wave_force, wave_elevation] = waveForceRAO(p.wave_time, waveModel, ...
    p.wave_speed, p.wave_heading, p.wave_propagation);
% MSS read_veres_WD defines F=Q*a^2. Sum diagonal mean surge independently.
mean_curve = zeros(size(raw.vessel.velocities));
angles = mod(p.wave_propagation+p.mu(:)-p.wave_heading,2*pi);
for j = 1:length(raw.vessel.velocities)
    table = interp1(raw.vessel.driftfrc.w(:),raw.vessel.driftfrc.amp{1}(:,:,j),p.omega(:),'linear');
    table = [table table(:,1)];
    directional = interp1([raw.vessel.headings(:);2*pi],table',angles,'linear')';
    mean_curve(j) = sum(sum(directional.*p.wave_amplitude.^2));
end
wave_mean_surge = interp1(raw.vessel.velocities,mean_curve,p.wave_speed,'linear');
save('-v7',output_path,'derivatives','trajectory','converted_M','converted_MA', ...
    'converted_D','converted_G','wave_force','wave_elevation','wave_mean_surge');
end
