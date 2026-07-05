import { apiGet } from './client'
import type { DeviceDetail, DeviceInfo } from '@/types'

export const getDevices = apiGet<DeviceInfo[]>('/devices/')
export const getDeviceDetail = apiGet<DeviceDetail, [deviceId: number]>(
    (deviceId) => `/devices/${deviceId}`,
)
