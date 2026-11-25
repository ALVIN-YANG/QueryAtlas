import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AnswerText } from "./AnswerText.js";

const render = (text) => renderToStaticMarkup(createElement(AnswerText, { text }));

test("renders bold phrases and keeps page citation numbers", () => {
  assert.equal(
    render("保存 **47 个自然日**，编号 **FJ-407**。\n见第 2 页[1]。"),
    "保存 <strong>47 个自然日</strong>，编号 <strong>FJ-407</strong>。\n见第 2 页[1]。",
  );
});

test("keeps unmatched markers and escapes model-provided HTML", () => {
  assert.equal(render("未闭合 **正文"), "未闭合 **正文");
  assert.equal(
    render("**<img src=x>** [1]"),
    "<strong>&lt;img src=x&gt;</strong> [1]",
  );
});
