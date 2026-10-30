function matched_container(mss_root, input_path, output_path)
% Replay the original MSS container.m derivative and identical RK4 trajectories.
addpath(genpath(mss_root));
p = load(input_path);

derivatives = zeros(size(p.derivative_states));
actuator_differences = zeros(size(p.derivative_states,1),6);
% Recover actuator-load differences from original accelerations. The hull terms
% cancel; the source coupled mass matrix converts acceleration back to SI force.
L = 175; scale = 0.5*1025*L^3;
mass4 = scale * [0.008158,0,0,0; ...
    0,0.014969,-0.007049*0.0313*L,0.007049*0.05*L; ...
    0,-0.007049*0.0313*L,0.0000210*L^2,0; ...
    0,0.007049*0.05*L,0,0.000875*L^2];
for k = 1:size(p.derivative_states, 1)
    derivatives(k,:) = container(p.derivative_states(k,:)', ...
        p.derivative_orders(k,:)')';
    neutral = p.derivative_states(k,:)'; neutral(9) = 0; neutral(10) = 70;
    baseline = container(neutral,p.derivative_orders(k,:)');
    delta_accel = derivatives(k,[1,2,7,3])' - baseline([1,2,7,3]);
    load = mass4 * delta_accel;
    actuator_differences(k,:) = [load(1),load(2),0,load(3),0,load(4)];
end

short = zeros(size(p.short_x0, 1), size(p.short_orders, 2) + 1, 10);
for case_index = 1:size(p.short_x0, 1)
    short(case_index,1,:) = p.short_x0(case_index,:);
    for k = 1:size(p.short_orders, 2)
        short(case_index,k+1,:) = rk4(@container, p.short_h, ...
            squeeze(short(case_index,k,:)), ...
            squeeze(p.short_orders(case_index,k,:)))';
    end
end

sustained = zeros(size(p.sustained_orders, 1) + 1, 10);
sustained(1,:) = p.sustained_x0(:)';
for k = 1:size(p.sustained_orders, 1)
    sustained(k+1,:) = rk4(@container, p.sustained_h, sustained(k,:)', ...
        p.sustained_orders(k,:)')';
end

save('-v7', output_path, 'derivatives', 'short', 'sustained', 'actuator_differences');
end
