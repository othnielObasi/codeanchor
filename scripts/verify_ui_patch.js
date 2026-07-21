/* Verify the Codex Recovery UI patch renders and uses only pre-existing CSS.
 *
 * Tailwind in HACKATHON_UI.html is PRECOMPILED into a static <style> block --
 * there is no runtime Tailwind. Any utility class not already in that block
 * silently renders as nothing. This gate catches that. (It found two real
 * cases: border-l-2 and border-amber-500, neither of which is compiled in.)
 *
 * Usage: node scripts/verify_ui_patch.js <patched-repo-root>
 */
const REPO = process.argv[2] || "/tmp/uitest";
global.document={getElementById:()=>({set innerHTML(v){},get innerHTML(){return"";}}),querySelectorAll:()=>[],addEventListener(){}};
global.window={matchMedia:()=>({matches:false,addEventListener(){}}),scrollTo(){},addEventListener(){}};
global.requestAnimationFrame=()=>{}; global.IntersectionObserver=class{observe(){}disconnect(){}};
global.fetch=async()=>{throw new Error("offline")};
const fs=require('fs');
const _src=fs.readFileSync(REPO+'/HACKATHON_UI.html','utf8');
const _blocks=[..._src.matchAll(/<script>([\s\S]*?)<\/script>/g)];
fs.writeFileSync('/tmp/_codex_ui_app.js',_blocks[_blocks.length-1][1]);
const {empty,full}=eval(fs.readFileSync('/tmp/_codex_ui_app.js','utf8')+`
const empty=codexRecoveryPanel();
codexResult=Object.assign({},CODEX_FIXTURE_RESULT,{offline:true});
const full=codexRecoveryPanel();({empty,full});`);

const checks=[["CTA in empty state",empty.includes("Run Codex Recovery Demo")],
 ["objective",full.includes("partial-refund support")],["constraint",full.includes("do NOT touch auth.py")],
 ["protected paths",full.includes("billing/auth/")],["compaction summary",full.includes("calculate_partial_refund")],
 ["drift policy id",full.includes("ctx-stale-block-v1")],["violation path",full.includes("billing/auth/eligibility.py")],
 ["timeline",full.includes("Recovery brief issued")],["offline badge",full.includes("Offline fixture")],
 ["violation badge",full.includes("Constraint violated")]];
let fail=0; for(const[n,ok]of checks){if(!ok)fail++;console.log(`  ${ok?"✓":"✗"} ${n}`);}

// GATE: every class we emit must exist in the precompiled stylesheet,
// otherwise it silently renders as nothing (Tailwind is static here).
const blob=fs.readFileSync(REPO + '/HACKATHON_UI.html','utf8');
const css=(blob.match(/<style[^>]*>([\s\S]*?)<\/style>/g)||[]).join("\n");
const used=[...new Set([...(empty+full).matchAll(/class="([^"]+)"/g)].flatMap(m=>m[1].split(/\s+/)).filter(Boolean))];
const undef=used.filter(c=>!css.includes("."+c.replace(/([.\[\]/:])/g,"\\$1")));
console.log(`\n  classes emitted: ${used.length}  undefined-in-CSS: ${undef.length}`);
if(undef.length){console.log("   -> "+undef.join(", "));fail++;}
console.log(fail?"\nFAIL":"\nALL CHECKS PASS");
process.exit(fail?1:0);
