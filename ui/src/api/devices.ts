import { apiGet } from './client'
import type { DeviceInfo } from '@/types'

export const getDevices = apiGet<DeviceInfo[]>('/devices/')
