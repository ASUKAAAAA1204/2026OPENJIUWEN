import type { Claim } from '../api/types'
import claims from './claims.json'

/** 证据夹具：后端接入后由 Report.claims 下发，前端不再读此文件 */
export const CLAIM_FIXTURE = claims as Claim[]
