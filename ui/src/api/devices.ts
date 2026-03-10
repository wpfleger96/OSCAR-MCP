import api from './client'
import type { DeviceInfo } from '@/types'

export async function getDevices(): Promise<DeviceInfo[]> {
    const { data } = await api.get<DeviceInfo[]>('/devices/')
    return data
}
