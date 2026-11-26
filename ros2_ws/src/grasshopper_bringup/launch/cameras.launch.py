"""Launch the configured Grasshopper rig in one Spinnaker process."""

import json
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode


def launch_setup(context):
    config_path = Path(LaunchConfiguration('config_file').perform(context))
    with config_path.open() as stream:
        config = json.load(stream)
    serial_filter = LaunchConfiguration('serial').perform(context)
    synchronized = LaunchConfiguration('synchronized').perform(context).lower() == 'true'
    cameras = config['cams']
    serials = [camera['serial'] for camera in cameras]
    if len(set(serials)) != len(serials):
        raise ValueError('Camera serials must be unique')
    if config['master_serial'] not in serials:
        raise ValueError('master_serial must be in cams')
    if serial_filter:
        cameras = [camera for camera in cameras if camera['serial'] == serial_filter]
        if not cameras:
            raise ValueError(f'Serial {serial_filter} is not in {config_path}')
        if synchronized and serial_filter != config['master_serial']:
            raise ValueError('A single follower needs synchronized:=false to run without its master')

    definitions = str(Path(get_package_share_directory('grasshopper_bringup'))
                      / 'config' / 'grasshopper.yaml')
    # The synchronized driver's first camera is the timestamp reference.
    cameras = sorted(cameras, key=lambda camera: camera['serial'] != config['master_serial'])
    nodes = []
    rig_parameters = {'cameras': [f'camera_{camera["serial"]}' for camera in cameras]}
    for camera in cameras:
        serial = camera['serial']
        master = serial == config['master_serial']
        params = {
            'serial_number': serial,
            'parameter_file': definitions,
            'frame_id': f'grasshopper_{serial}_optical_frame',
            'adjust_timestamp': True,
            'compute_brightness': False,
            'buffer_queue_size': 4,
            'image_queue_size': 4,
            'acquisition_timeout': 30.0,
            'trigger_selector': 'FrameStart',
            'trigger_mode_off': 'Off',
            'video_mode': config['video_mode'],
            'binning_vertical': int(config['binning_vertical']),
            # Spinnaker's enum is "RGB8"; the driver publishes ROS encoding "rgb8".
            'pixel_format': config['pixel_format'],
            'offset_x_reset': 0,
            'offset_y_reset': 0,
            'image_width': int(config['img_width']),
            'image_height': int(config['img_height']),
            'offset_x': int(camera['img_offsetX']),
            'offset_y': int(camera['img_offsetY']),
            'gain_auto': 'Off',
            'gain': float(camera['gain']),
            'black_level': float(camera['black_level']),
            'exposure_auto': 'Off',
            'exposure_mode': 'Timed',
            'exposure_time': float(camera['exposure_time']),
            'acquisition_mode': 'Continuous',
            'frame_rate_enable': master or not synchronized,
            'trigger_mode': 'On' if synchronized and not master else 'Off',
            'chunk_mode_active': True,
            'chunk_selector_frame_counter': 'FrameCounter',
            'chunk_enable_frame_counter': True,
            'chunk_selector_exposure_time': 'ExposureTime',
            'chunk_enable_exposure_time': True,
            'chunk_selector_gain': 'Gain',
            'chunk_enable_gain': True,
            'chunk_selector_timestamp': 'Timestamp',
            'chunk_enable_timestamp': True,
        }
        if master or not synchronized:
            params.update({
                'frame_rate_auto': 'Off',
                'frame_rate': float(config['fps']),
            })
        if synchronized:
            params.update({
                'line_selector': config['master_line'] if master else config['slave_line'],
                'line_mode': 'Output' if master else 'Input',
            })
            if master:
                params['line_source'] = 'ExposureActive'
            else:
                params.update({
                    'trigger_source': config['slave_line'],
                    'trigger_activation': 'RisingEdge',
                    'trigger_overlap': 'ReadOut',
                })
        nodes.append(ComposableNode(
            package='spinnaker_camera_driver',
            plugin='spinnaker_camera_driver::CameraDriver',
            name=f'camera_{serial}',
            namespace='grasshopper',
            parameters=[params],
            extra_arguments=[{'use_intra_process_comms': True}],
        ))
        rig_parameters.update({f'camera_{serial}.{key}': value for key, value in params.items()})
    if synchronized and not serial_filter:
        nodes = [ComposableNode(
            package='spinnaker_synchronized_camera_driver',
            plugin='spinnaker_synchronized_camera_driver::SynchronizedCameraDriver',
            name='grasshopper',
            parameters=[rig_parameters],
            extra_arguments=[{'use_intra_process_comms': True}],
        )]
    return [ComposableNodeContainer(
        name='grasshopper_container', namespace='',
        package='rclcpp_components', executable='component_container_mt',
        composable_node_descriptions=nodes, output='screen',
    )]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('config_file',
                              default_value='/home/docker/config/flir_grashopper.json',
                              description='JSON rig configuration file'),
        DeclareLaunchArgument('synchronized', default_value='true', choices=['true', 'false'],
                              description='Master ExposureActive output triggers followers on Line3'),
        DeclareLaunchArgument('serial', default_value='',
                              description='Test one configured serial; empty launches all cameras'),
        OpaqueFunction(function=launch_setup),
    ])
