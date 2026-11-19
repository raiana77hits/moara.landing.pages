/* Funções que as três telas usam. Nada de framework: só JavaScript puro. */
window.P = (function () {
  "use strict";

  var clienteId = localStorage.getItem("painel_cliente") || "";

  function el(id) {
    return document.getElementById(id);
  }

  function cliente() {
    return clienteId || "";
  }

  function reais(v) {
    if (v === null || v === undefined || v === "") { return ""; }
    var n = Number(v);
    if (isNaN(n)) { return String(v); }
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* Toda chamada ao painel passa por aqui. Erro do servidor vira mensagem em português. */
  async function api(url, corpo, cru) {
    var opcoes = { headers: {} };
    if (corpo !== undefined) {
      opcoes.method = "POST";
      if (cru) {
        opcoes.body = corpo;
      } else {
        opcoes.headers["Content-Type"] = "application/json";
        opcoes.body = JSON.stringify(corpo);
      }
    }
    var r;
    try {
      r = await fetch(url, opcoes);
    } catch (e) {
      throw new Error("Não consegui falar com o painel. A janela preta ainda está aberta?");
    }
    var texto = await r.text();
    var dados = null;
    try { dados = JSON.parse(texto); } catch (e) { dados = null; }
    if (!r.ok) {
      throw new Error((dados && dados.erro) || ("O painel respondeu com erro " + r.status + "."));
    }
    if (dados === null) {
      throw new Error("O painel respondeu uma coisa que eu não entendi.");
    }
    if (dados.erro) { throw new Error(dados.erro); }
    return dados;
  }

  function aplicarMarca(marca) {
    var r = document.documentElement.style;
    if (marca.cor) { r.setProperty("--cor", marca.cor); }
    if (marca.cor_escura) { r.setProperty("--cor-escura", marca.cor_escura); }
  }

  /* O seletor de cliente, igual nas três telas. */
  async function montarClientes(seletor, aoTrocar) {
    var r = await api("/api/clientes" + (clienteId ? "?cliente=" + clienteId : ""));
    clienteId = String(r.atual);
    localStorage.setItem("painel_cliente", clienteId);
    seletor.innerHTML = "";
    r.clientes.forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.nome;
      seletor.append(o);
    });
    var novo = document.createElement("option");
    novo.value = "__novo__";
    novo.textContent = "+ Novo cliente...";
    seletor.append(novo);
    seletor.value = clienteId;
    seletor.addEventListener("change", async function () {
      if (seletor.value === "__novo__") {
        var nome = prompt("Nome do novo cliente:");
        seletor.value = clienteId;
        if (!nome) { return; }
        try {
          var c = await api("/api/cliente", { nome: nome });
          clienteId = String(c.id);
          localStorage.setItem("painel_cliente", clienteId);
          location.reload();
        } catch (e) {
          alert(e.message);
        }
        return;
      }
      clienteId = seletor.value;
      localStorage.setItem("painel_cliente", clienteId);
      if (aoTrocar) { aoTrocar(); }
    });
    return r;
  }

  function erroNaTela(e) {
    var d = document.createElement("div");
    d.className = "aviso aviso-erro";
    d.textContent = "Deu um problema: " + (e && e.message ? e.message : e);
    document.querySelector("main").prepend(d);
  }

  return {
    el: el, api: api, reais: reais, cliente: cliente,
    aplicarMarca: aplicarMarca, montarClientes: montarClientes, erroNaTela: erroNaTela
  };
})();
