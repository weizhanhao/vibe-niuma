/// <reference types="vite/client" />

// Plan 6 · Task 7：允许 `import help from './x.md?raw';` 这种语法
// 拿到 markdown 文件原始字符串，bundle 进 dist 不走 markdown 编译。
declare module '*.md?raw' {
  const content: string;
  export default content;
}
