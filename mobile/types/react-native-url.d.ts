// RN 内部 URL 实现的类型声明（M3-1）。
//
// 为什么要引它：`react-native/Libraries/Blob/URL` 是**真机上 `globalThis.URL` 的实体**，
// 而 jest 里的全局 URL 是 Node 的规范实现——两者对同一个字符串给出不同答案
// （见 test/merchantCards.test.ts 顶部）。只有显式导入它，测试量的才是设备行为。
//
// 只声明用得到的 getter；写成 `any` 会把「我们依赖 RN 有 protocol/username/password
// 这三个 getter」这条前提又变回默认假设，而这条前提正是那组测试要钉的东西。
declare module 'react-native/Libraries/Blob/URL' {
  export class URL {
    constructor(url: string, base?: string | URL)
    readonly hash: string
    readonly host: string
    readonly hostname: string
    readonly href: string
    readonly origin: string
    readonly password: string
    readonly pathname: string
    readonly port: string
    readonly protocol: string
    readonly search: string
    readonly username: string
    toString(): string
  }
}
