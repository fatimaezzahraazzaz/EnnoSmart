// À coller dans la console du navigateur sur http://localhost:3000
console.log("LOCALSTORAGE KEYS", Object.keys(localStorage))
console.log("SESSIONSTORAGE KEYS", Object.keys(sessionStorage))

for (const [k, v] of Object.entries(localStorage)) {
  if ((v || "").includes(".") || k.toLowerCase().includes("token") || k.toLowerCase().includes("auth")) {
    console.log("LOCAL", k, v)
  }
}
for (const [k, v] of Object.entries(sessionStorage)) {
  if ((v || "").includes(".") || k.toLowerCase().includes("token") || k.toLowerCase().includes("auth")) {
    console.log("SESSION", k, v)
  }
}
