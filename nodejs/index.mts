import { EufySecurity, LogLevel, P2PConnectionType, type Logger } from "eufy-security-client";
import {InfluxDB, Point } from '@influxdata/influxdb-client'
import cron from 'node-cron';

const url = 'http://' + (process.env.INFLUX_HOST || 'localhost:8086')
const token =  process.env.INFLUX_TOKEN!
const org =  process.env.INFLUX_ORG!
const bucket =  process.env.INFLUX_BUCKET!

const username = process.env.EUFY_USERNAME!
const password = process.env.EUFY_PASSWORD!
const country = process.env.EUFY_COUNTRY || 'se'

const task = cron.schedule('*/5 * * * *', eufy, {});

// Immediately execute the task once at startup
console.log('INFO', '[node]', 'Run task immediately');
task.execute()

// Start the task to run according to the schedule
console.log('INFO', '[node]', 'Starting scheduler');
task.start()

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
    eufyClient.on("captcha request", (captchaId: string, captcha: string) => { 
        console.error('ERROR', '[eufy]', 'Captcha required:', captchaId, captcha);
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
    await eufyClient.connect();
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