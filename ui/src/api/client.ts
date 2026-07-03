import axios from 'axios'
import type { AxiosError, AxiosRequestConfig } from 'axios'

// No default Content-Type: axios infers it per request (JSON for plain objects,
// multipart with boundary for FormData). A global application/json default makes
// axios JSON-serialize FormData bodies, breaking file uploads.
const api = axios.create({
    baseURL: '/api/v1',
})

api.interceptors.response.use(
    (response) => response,
    (error: AxiosError<{ message?: string; detail?: string | Array<{ msg: string }> }>) => {
        const data = error.response?.data
        if (data) {
            if (typeof data.message === 'string') {
                error.message = data.message
            } else if (typeof data.detail === 'string') {
                error.message = data.detail
            } else if (Array.isArray(data.detail)) {
                const msgs = data.detail
                    .map((d) => d.msg)
                    .filter(Boolean)
                    .join('; ')
                if (msgs) error.message = msgs
            }
        }
        return Promise.reject(error)
    },
)

type HttpMethod = 'get' | 'post' | 'patch' | 'put' | 'delete'

/** Endpoint path: a literal, or a builder over the endpoint's arguments. */
type PathSpec<A extends unknown[]> = string | ((...args: A) => string)

/** Maps the endpoint's arguments to axios config (params, data, signal, ...). */
type ConfigSpec<A extends unknown[]> = (...args: A) => AxiosRequestConfig

/**
 * Build a typed API call. The returned function applies the path builder and
 * config mapper to its arguments and resolves with `response.data`.
 *
 *     const getThing = createApiEndpoint<Thing, [id: number]>('get', (id) => `/things/${id}`)
 */
export function createApiEndpoint<T, A extends unknown[] = []>(
    method: HttpMethod,
    path: PathSpec<A>,
    toConfig?: ConfigSpec<A>,
): (...args: A) => Promise<T> {
    return async (...args: A): Promise<T> => {
        const url = typeof path === 'function' ? path(...args) : path
        const { data } = await api.request<T>({ method, url, ...toConfig?.(...args) })
        return data
    }
}

export const apiGet = <T, A extends unknown[] = []>(path: PathSpec<A>, toConfig?: ConfigSpec<A>) =>
    createApiEndpoint<T, A>('get', path, toConfig)

export const apiPost = <T, A extends unknown[] = []>(path: PathSpec<A>, toConfig?: ConfigSpec<A>) =>
    createApiEndpoint<T, A>('post', path, toConfig)

export const apiPatch = <T, A extends unknown[] = []>(
    path: PathSpec<A>,
    toConfig?: ConfigSpec<A>,
) => createApiEndpoint<T, A>('patch', path, toConfig)

export const apiDelete = <T, A extends unknown[] = []>(
    path: PathSpec<A>,
    toConfig?: ConfigSpec<A>,
) => createApiEndpoint<T, A>('delete', path, toConfig)

/** Like {@link apiGet}, but resolves `null` when the API responds 204 No Content. */
export function apiGetOrNull<T, A extends unknown[] = []>(
    path: PathSpec<A>,
    toConfig?: ConfigSpec<A>,
): (...args: A) => Promise<T | null> {
    return async (...args: A): Promise<T | null> => {
        const url = typeof path === 'function' ? path(...args) : path
        const { data, status } = await api.get<T>(url, {
            ...toConfig?.(...args),
            validateStatus: (s) => s === 200 || s === 204,
        })
        return status === 204 ? null : data
    }
}

export default api
