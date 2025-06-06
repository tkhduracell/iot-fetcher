import { EufySecurity, P2PConnectionType } from "eufy-security-client";
import {InfluxDB, Point } from '@influxdata/influxdb-client'
import cron from 'node-cron';

const url = 'http://' + (process.env.INFLUX_HOST || 'localhost:8086')
const token =  process.env.INFLUX_TOKEN!
const org =  process.env.INFLUX_ORG!
const bucket =  process.env.INFLUX_BUCKET!

const username = process.env.EUFY_USERNAME!
const password = process.env.EUFY_PASSWORD!
const country = process.env.EUFY_COUNTRY || 'se'

const task = cron.schedule('*/30 * * * *', eufy, {});

// Immediately execute the task once at startup
task.execute()

// Start the task to run according to the schedule
task.start()

async function eufy() {
    const writeApi = new InfluxDB({url, token, timeout: 60_000 }).getWriteApi(org, bucket, 's')
    const eufyClient = await EufySecurity.initialize({
        username,
        password,
        country,
        pollingIntervalMinutes: 5,
        p2pConnectionSetup: P2PConnectionType.ONLY_LOCAL,
        eventDurationSeconds: 60,
    });
    
    console.log('INFO', 'Looking for devices...');
    await eufyClient.connect()

    const devices = await eufyClient.getDevices()
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
        console.log(point.toLineProtocol())
        points.push(point)
    }

    console.log('INFO', 'Writing points to InfluxDB...');
    await writeApi.writePoints(points);
    console.log('INFO', 'Points written successfully!');
    await eufyClient.close();
}