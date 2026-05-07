import axios, { type AxiosInstance } from 'axios'
import { ElMessage } from 'element-plus'

const instance: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

instance.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message || 'Request failed'
    ElMessage.error(String(msg))
    return Promise.reject(err)
  },
)

const http = {
  get:    <T = unknown>(url: string, config?: object): Promise<T> =>
    instance.get(url, config),
  post:   <T = unknown>(url: string, data?: unknown, config?: object): Promise<T> =>
    instance.post(url, data, config),
  put:    <T = unknown>(url: string, data?: unknown, config?: object): Promise<T> =>
    instance.put(url, data, config),
  delete: <T = unknown>(url: string, config?: object): Promise<T> =>
    instance.delete(url, config),
}

export default http
