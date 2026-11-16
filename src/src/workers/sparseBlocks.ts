/**
 * A block file held in part: its footer, and the row groups some read has
 * needed, in a buffer the size of the whole file.
 *
 * A phone's street view reads two of a block's sixteen cells. Central
 * Boston's block is 4.1 MB, and on a phone's link that file was the last
 * thing the first trees waited for; the row groups those two cells fall in
 * are 1.6 MB of it. The engine could range-read them itself, but it reads a
 * remote file one synchronous request at a time with a read-ahead that grows
 * past every seek (22 requests and more bytes than the file, measured). So
 * the worker reads the footer (parquetFooter.ts), fetches the row groups a
 * box needs side by side, and hands the engine the buffer as if it were the
 * file. The engine prunes row groups by the same rule and never looks at the
 * rest.
 *
 * What was not fetched is filled with 0xFF, not left zero: should the engine
 * ever read there, 0xFF is no page header and the read throws, where a run of
 * zeros parses as an empty one. The caller then falls back to the whole file.
 */
import { footerLength, parquetRowGroups, rowGroupsInBox, type RowGroupSpan } from './parquetFooter'

/** Bytes `[start, end)` of a file, or its last `suffix` bytes; the answer says where they start and how long the file is. */
export type RangeRequest = { start: number; end: number } | { suffix: number }
export type RangeFetch = (file: string, range: RangeRequest) => Promise<{ bytes: Uint8Array; start: number; total: number }>

/** Enough of a file's end to hold a block's footer in one request (Boston's largest is 21 KB). */
const TAIL_BYTES = 64 * 1024
const MAGIC = new Uint8Array([0x50, 0x41, 0x52, 0x31]) // PAR1

export type Box = { west: number; east: number; south: number; north: number }

export class SparseBlock {
  /** The file's bytes where fetched, 0xFF elsewhere. */
  readonly bytes: Uint8Array
  /** Bytes fetched so far, footer included. */
  fetched = 0
  /** Requests made so far. */
  requests = 0
  private readonly held = new Set<number>()
  private readonly loading = new Map<number, Promise<void>>()

  private constructor(
    readonly file: string,
    readonly groups: RowGroupSpan[],
    total: number,
    private readonly fetchRange: RangeFetch,
  ) {
    this.bytes = new Uint8Array(total).fill(0xff)
    this.bytes.set(MAGIC, 0)
  }

  get total(): number {
    return this.bytes.length
  }

  /** Read the file's footer: one request, or two for a footer longer than the first guess. */
  static async open(file: string, fetchRange: RangeFetch, tailBytes = TAIL_BYTES): Promise<SparseBlock> {
    let tail = await fetchRange(file, { suffix: tailBytes })
    let requests = 1
    const need = footerLength(tail.bytes)
    if (need > tail.bytes.length) {
      const rest = await fetchRange(file, { start: tail.total - need, end: tail.start })
      requests += 1
      const joined = new Uint8Array(rest.bytes.length + tail.bytes.length)
      joined.set(rest.bytes, 0)
      joined.set(tail.bytes, rest.bytes.length)
      tail = { bytes: joined, start: rest.start, total: tail.total }
    }
    if (tail.start + tail.bytes.length !== tail.total) throw new Error(`sparse block ${file}: the tail does not end the file`)
    const block = new SparseBlock(file, parquetRowGroups(tail.bytes), tail.total, fetchRange)
    block.put(tail.start, tail.bytes)
    block.requests = requests
    // A small file's tail is the file, or most of it.
    for (const [index, group] of block.groups.entries()) if (group.start >= tail.start) block.held.add(index)
    return block
  }

  private put(start: number, bytes: Uint8Array): void {
    this.bytes.set(bytes, start)
    this.fetched += bytes.length
  }

  /**
   * Hold every row group a read of `box` touches. Row groups next to each
   * other are one request; the requests run side by side. A group another
   * call is already fetching is waited for, not fetched again.
   */
  async cover(box: Box): Promise<void> {
    const wanted = rowGroupsInBox(this.groups, box)
    const runs: number[][] = []
    for (const index of wanted) {
      if (this.held.has(index) || this.loading.has(index)) continue
      const last = runs[runs.length - 1]
      if (last && last[last.length - 1] === index - 1) last.push(index)
      else runs.push([index])
    }
    for (const run of runs) {
      const start = this.groups[run[0]].start
      const end = this.groups[run[run.length - 1]].end
      const load = this.fetchRange(this.file, { start, end }).then((part) => {
        this.requests += 1
        if (part.start !== start || part.bytes.length !== end - start) throw new Error(`sparse block ${this.file}: asked for ${start}-${end}, got ${part.bytes.length} from ${part.start}`)
        this.put(start, part.bytes)
        for (const index of run) this.held.add(index)
      }).finally(() => {
        for (const index of run) this.loading.delete(index)
      })
      for (const index of run) this.loading.set(index, load)
    }
    await Promise.all(wanted.flatMap((index) => this.loading.get(index) ?? []))
    const missing = wanted.filter((index) => !this.held.has(index))
    if (missing.length) throw new Error(`sparse block ${this.file}: row groups ${missing.join(', ')} were not fetched`)
  }
}
