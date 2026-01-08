import { EufySecurity, LogLevel, P2PConnectionType, type Logger } from "eufy-security-client";
import {InfluxDB, Point } from '@influxdata/influxdb-client'
import { cloudLogin, loginDevice, loginDeviceByIp } from 'tp-link-tapo-connect';
import cron from 'node-cron';

import { writeFile } from 'node:fs/promises'

const url = 'http://' + (process.env.INFLUX_HOST || 'localhost:8086')
const token =  process.env.INFLUX_TOKEN!
const org =  process.env.INFLUX_ORG!
const bucket =  process.env.INFLUX_BUCKET!

const username = process.env.EUFY_USERNAME!
const password = process.env.EUFY_PASSWORD!
const country = process.env.EUFY_COUNTRY || 'se'

const tapoEmail = process.env.TAPO_EMAIL!
const tapoPassword = process.env.TAPO_PASSWORD!

const task = cron.schedule('*/5 * * * *', eufy, {});
const tapoTask = cron.schedule('*/10 * * * *', tapo, {});

// Immediately execute the task once at startup
console.log('INFO', '[node]', 'Run eufy task immediately');
task.execute()

console.log('INFO', '[node]', 'Run tapo task immediately');
tapoTask.execute()

// Start the task to run according to the schedule
console.log('INFO', '[node]', 'Starting eufy scheduler');
task.start()

console.log('INFO', '[node]', 'Starting tapo scheduler');
tapoTask.start()

let eufyClient: EufySecurity | null = null;

async function eufy() {
    try {
        await _eufyInit();
        await _eufy();
    } catch (error) {
        console.error('ERROR', '[eufy]', 'Failed to run eufy task:', error);
    }
}

async function _eufyInit() {
    if (eufyClient?.isConnected()) {
        console.log('INFO', '[eufy]', 'Eufy client already initialized and connected.');
        return;
    }
    eufyClient = await EufySecurity.initialize({
        username,
        password,
        country,
        logging: { level: LogLevel.Warn },
        pollingIntervalMinutes: 5,
        p2pConnectionSetup: P2PConnectionType.ONLY_LOCAL,
        eventDurationSeconds: 60,
    }, {
        error: (message, err) => console.error('ERROR', '[eufy]', message, err),
        debug: (message) => console.debug('DEBUG', '[eufy]', message),
        info: (message) => console.info('INFO', '[eufy]', message),
        warn: (message) => console.warn('WARN', '[eufy]', message),
        trace: (message) => console.trace('TRACE', '[eufy]', message),
    } as Logger);

    eufyClient.on('connect', () => {
        console.log('INFO', '[eufy]', 'Connected to Eufy Security');
    });
    eufyClient.on('close', () => {
        console.log('INFO', '[eufy]', 'Disconnected from Eufy Security');
    });
    eufyClient.on("captcha request", async (captchaId: string, captcha: string) => {
        const captchaFilePath = `/tmp/captcha_${captchaId}.txt`;
        console.error('ERROR', '[eufy]', 'Captcha', captchaId, 'on file', captchaFilePath);
        try {
            await writeFile(captchaFilePath, captcha);
        } catch (err) {
            console.error('ERROR', '[eufy]', `Failed to write captcha file ${captchaFilePath}:`, err);
        }
    });
    eufyClient.on('station connect', (station) => {
        console.log('INFO', '[eufy]', 'Connected to station:', station.getName(), station.getSerial());
    });
    eufyClient.on('station close', (station) => {
        console.log('INFO', '[eufy]', 'Disconnected from station:', station.getName(), station.getSerial());
    });
    eufyClient.on('station connection error', (station, error) => {
        console.error('ERROR', '[eufy]', 'Station connection error:', station.getName(), station.getSerial(), error);
    });

    console.log('INFO', '[eufy]', 'Connecting to Eufy station...');
    try {
        await eufyClient.connect();
    } catch (error) {
        console.error('ERROR', '[eufy]', 'Failed to connect to Eufy:', error);
    }
    
}

async function _eufy() {
    const writeApi = new InfluxDB({url, token, timeout: 60_000 }).getWriteApi(org, bucket, 's')
    
    const devices = await eufyClient.getDevices()
    console.log('INFO', '[eufy]', `Found ${devices.length} devices.`);
    const points : Point[] = []
    for (const device of devices) {
        const {
            motionDetected,
            personDetected,
            soundDetected,
            petDetected,
            identityPersonDetected,
            strangerPersonDetected,
            vehicleDetected,
            detectionStatisticsWorkingDays,
            detectionStatisticsDetectedEvents,
            detectionStatisticsRecordedEvents,
            speakerVolume,
            wifiRssi,
            wifiSignalLevel,
            chargingStatus,
            battery,
            powerWorkingMode,
            batteryTemperature
        } = device.getProperties()
        
        const point = new Point('eufy_device')
            .tag('device_sn', device.getSerial())
            .tag('device_name', device.getName())
            .tag('device_model', device.getModel())
            .booleanField('motionDetected', motionDetected)
            .booleanField('personDetected', personDetected)
            .booleanField('soundDetected', soundDetected)
            .booleanField('petDetected', petDetected)
            .booleanField('identityPersonDetected', identityPersonDetected)
            .booleanField('strangerPersonDetected', strangerPersonDetected)
            .booleanField('vehicleDetected', vehicleDetected)
            .intField('detectionStatisticsWorkingDays', detectionStatisticsWorkingDays)
            .intField('detectionStatisticsDetectedEvents', detectionStatisticsDetectedEvents)
            .intField('detectionStatisticsRecordedEvents', detectionStatisticsRecordedEvents)
            .intField('speakerVolume', speakerVolume)
            .intField('wifiRssi', wifiRssi)
            .intField('wifiSignalLevel', wifiSignalLevel)

        if (device.hasBattery()) {
            point
                .intField('chargingStatus', chargingStatus ?? 0)
                .intField('battery', battery ?? -1)
                .intField('powerWorkingMode', powerWorkingMode ?? -1)
                .intField('batteryTemperature', batteryTemperature ?? -1)
        }
        console.log('INFO', '[eufy]', point.toLineProtocol())
        points.push(point)

        /*
        if (device.isCamera() && device.hasCommand(CommandName.DeviceStartLivestream)) {
            await eufyClient.setCameraMaxLivestreamDuration(60);
            await eufyClient.startStationLivestream(device.getSerial());
            const station = await eufyClient.getStation(device.getStationSN());

        }*/
    }

    console.log('INFO', '[eufy]', 'Writing points to InfluxDB...');
    await writeApi.writePoints(points);
}

function decodeIfBase64(s: string): string {
    if (!s || s.length % 4 !== 0) return s;

    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) return s;

    try {
        const decoded = Buffer.from(s, 'base64').toString('utf-8');
        return decoded;
    } catch {
        return s;
    }
}

async function tapo() {
    try {
        console.log('INFO', '[tapo]', 'Starting TP-Link Tapo devices discovery...');
        await _tapo();
    } catch (error) {
        console.error('ERROR', '[tapo]', 'Failed to run tapo task:', error);
    }
}

async function _tapo() {
    const writeApi = new InfluxDB({url, token, timeout: 60_000 }).getWriteApi(org, bucket, 's')
    
    try {
        // Login to TP-Link cloud
        console.log('INFO', '[tapo]', 'Logging in to TP-Link cloud...');
        const cloudApi = await cloudLogin(tapoEmail, tapoPassword);
        
        // Discover smart plugs
        console.log('INFO', '[tapo]', 'Discovering SMART.TAPOPLUG devices...');
        const devices = await cloudApi.listDevicesByType('SMART.TAPOPLUG');
        console.log('INFO', '[tapo]', `Found ${devices.length} SMART.TAPOPLUG devices.`);
        
        const points: Point[] = []
        
        for (const deviceInfo of devices) {
            try {
                const decodedAlias = decodeIfBase64(deviceInfo.alias);
                console.log('INFO', '[tapo]', `Connecting to device: ${decodedAlias} (${deviceInfo.deviceMac})`);

                // Login to the individual device
                const device = await loginDevice(tapoEmail, tapoPassword, deviceInfo);

                // Get device info and status
                const deviceInfoResponse = await device.getDeviceInfo();
                const deviceUsage = await device.getEnergyUsage().catch(() => null); // Some devices may not support energy usage

                console.log('INFO', '[tapo]', `Device ${decodedAlias} - Power: ${deviceInfoResponse.device_on ? 'ON' : 'OFF'}`);

                const point = new Point('tapo_device')
                    .tag('device_id', deviceInfo.deviceId)
                    .tag('device_mac', deviceInfo.deviceMac)
                    .tag('device_alias', decodedAlias || deviceInfo.deviceMac)
                    .tag('device_model', deviceInfo.deviceModel)
                    .tag('device_type', deviceInfo.deviceType)
                    .booleanField('device_on', deviceInfoResponse.device_on)
                    .intField('on_time', deviceInfoResponse.on_time || 0)
                    .intField('signal_level', deviceInfoResponse.signal_level || 0)
                    .intField('rssi', deviceInfoResponse.rssi || 0);
                
                // Add energy usage data if available
                if (deviceUsage) {
                    if (deviceUsage.current_power !== undefined) {
                        point.floatField('current_power_mw', deviceUsage.current_power);
                    }
                    if (deviceUsage.today_energy !== undefined) {
                        point.floatField('today_energy_wh', deviceUsage.today_energy);
                    }
                    if (deviceUsage.month_energy !== undefined) {
                        point.floatField('month_energy_wh', deviceUsage.month_energy);
                    }
                }
                
                console.log('INFO', '[tapo]', point.toLineProtocol());
                points.push(point);
                
            } catch (deviceError) {
                const decodedAlias = decodeIfBase64(deviceInfo.alias);
                console.error('ERROR', '[tapo]', `Failed to get data from device ${decodedAlias}:`, deviceError);
            }
        }
        
        if (points.length > 0) {
            console.log('INFO', '[tapo]', `Writing ${points.length} points to InfluxDB...`);
            await writeApi.writePoints(points);
        } else {
            console.log('INFO', '[tapo]', 'No data points to write to InfluxDB.');
        }
        
    } catch (error) {
        console.error('ERROR', '[tapo]', 'Failed to discover or connect to TP-Link devices:', error);
    }
}