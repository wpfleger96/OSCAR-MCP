import { apiGet } from './client'
import type { AboutInfo } from '@/types'

export const getAbout = apiGet<AboutInfo>('/about')
