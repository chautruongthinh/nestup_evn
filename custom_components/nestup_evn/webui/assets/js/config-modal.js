// Config Modal Logic (migrated from inline index.html)
function showConfigModal() {
    console.log('showConfigModal called');
    document.getElementById('configModal').classList.remove('hidden');
    loadConfig();
    fetchLogs();
    if (configLogsInterval) clearInterval(configLogsInterval);
    configLogsInterval = setInterval(fetchLogs, 2000);
}
function closeConfigModal() {
    console.log('closeConfigModal called');
    var modal = document.getElementById('configModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('show');
    }
    if (configLogsInterval) clearInterval(configLogsInterval);
    configLogsInterval = null;
}
// ĐẢM BẢO: Không gọi showConfigModal() khi load trang!
// Chỉ gán onclick cho nút Config:
function addAccount(userevn = '', passevn = '', server = '') {
  const row = document.createElement('div');
  row.className = 'account-row flex gap-2 mb-2';
  // Tạo input user
  const userInput = document.createElement('input');
  userInput.type = 'text';
  userInput.placeholder = 'User EVN';
  userInput.value = userevn;
  userInput.required = true;
  userInput.className = 'p-2 rounded border bg-gray-900 text-white flex-1 min-w-0 max-w-[180px]';
  // Tạo input pass
  const passInput = document.createElement('input');
  passInput.type = 'password';
  passInput.placeholder = 'Password';
  passInput.value = passevn;
  passInput.required = true;
  passInput.className = 'p-2 rounded border bg-gray-900 text-white flex-1 min-w-0 max-w-[180px]';
  // Tạo select server
  const serverSelect = document.createElement('select');
  serverSelect.className = 'p-2 rounded border bg-gray-900 text-white flex-[0.8] min-w-[90px] max-w-[120px]';
  const serverOptions = ['', 'npc', 'spc', 'hn', 'cpc', 'tl', 'hcmc'];
  serverOptions.forEach(opt => {
    const option = document.createElement('option');
    option.value = opt;
    option.textContent = opt ? opt.toUpperCase() : '-- Server --';
    if (opt === server) option.selected = true;
    serverSelect.appendChild(option);
  });
  // Nút xóa
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.title = 'Xóa';
  removeBtn.innerHTML = '&times;';
  removeBtn.className = 'bg-red-600 text-white rounded px-3';
  removeBtn.onclick = function() { row.remove(); };
  // Gắn vào row
  row.appendChild(userInput);
  row.appendChild(passInput);
  row.appendChild(serverSelect);
  row.appendChild(removeBtn);
  document.getElementById('accountsList').appendChild(row);
}

document.getElementById('addAccountBtn').onclick = function(e) {
  e.preventDefault();
  addAccount();
};

function showMsg(msg, error) {
  const el = document.getElementById('msg');
  el.textContent = msg;
  el.className = 'msg' + (error ? ' error' : '');
}

// Lấy base URL cho Ingress (Home Assistant)
function getBaseUrl() {
  const match = window.location.pathname.match(/\/api\/hassio_ingress\/[^\/]+/);
  return match ? match[0] : '';
}

async function loadConfig() {
  try {
    const res = await fetch(getBaseUrl() + '/config/data/options.json');
    if (!res.ok) throw new Error('Không thể tải options.json');
    const data = await res.json();
    document.getElementById('gemini_api_key').value = data.gemini_api_key || '';
    document.getElementById('accountsList').innerHTML = '';
    let accounts = [];
    try { accounts = JSON.parse(data.accounts_json || '[]'); } catch {}
    accounts.forEach(acc => addAccount(acc.userevn, acc.passevn, acc.server));
    document.getElementById('gemini_model').value = data.gemini_model || 'gemini-2.0-flash';
    document.getElementById('threadId').value = data.threadId || '';
    document.getElementById('type').value = data.type || '';
    document.getElementById('accountSelection').value = data.accountSelection || '';
    document.getElementById('urlzalo').value = data.urlzalo || '';
    document.getElementById('telegram_token').value = data.telegram_token || '';
    document.getElementById('telegram_chat_id').value = data.telegram_chat_id || '';
    document.getElementById('telegram_thread_id').value = data.telegram_thread_id || '';
  } catch (e) {
    showMsg('Không thể tải options.json: ' + e.message, true);
  }
}

document.getElementById('configForm').onsubmit = async function(e) {
  e.preventDefault();
  const gemini_api_key = document.getElementById('gemini_api_key').value.trim();
  const gemini_model = document.getElementById('gemini_model').value.trim();
  const accounts = [];
  document.querySelectorAll('.account-row').forEach(row => {
    const inputs = row.querySelectorAll('input, select');
    const userevn = inputs[0].value.trim();
    const passevn = inputs[1].value.trim();
    const server = inputs[2].tagName === 'SELECT' ? inputs[2].value.trim() : (inputs[2].value.trim() || '');
    if (userevn && passevn) accounts.push({userevn, passevn, server});
  });
  const threadId = document.getElementById('threadId').value.trim();
  const type = document.getElementById('type').value.trim();
  const accountSelection = document.getElementById('accountSelection').value.trim();
  const urlzalo = document.getElementById('urlzalo').value.trim();
  const telegram_token = document.getElementById('telegram_token').value.trim();
  const telegram_chat_id = document.getElementById('telegram_chat_id').value.trim();
  const telegram_thread_id = document.getElementById('telegram_thread_id').value.trim();
  if (!gemini_api_key || !gemini_model) {
    showMsg('Vui lòng nhập đủ Gemini API Key và Model!', true); return;
  }
  if (accounts.length === 0) {
    showMsg('Phải có ít nhất 1 tài khoản!', true); return;
  }
  const config = {
    accounts_json: JSON.stringify(accounts),
    gemini_api_key,
    gemini_model,
    threadId,
    type,
    accountSelection,
    urlzalo,
    telegram_token,
    telegram_chat_id,
    telegram_thread_id
  };
  try {
    const res = await fetch(getBaseUrl() + '/config/data/options.json', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config)
    });
    if (!res.ok) throw new Error('Lưu thất bại!');
    showMsg('Đã lưu cấu hình thành công!');
  } catch (e) {
    showMsg('Lỗi khi lưu: ' + e.message, true);
  }
};

document.getElementById('saveAndRestartBtn').onclick = async function(e) {
  e.preventDefault();
  const gemini_api_key = document.getElementById('gemini_api_key').value.trim();
  const gemini_model = document.getElementById('gemini_model').value.trim();
  const accounts = [];
  document.querySelectorAll('.account-row').forEach(row => {
    const inputs = row.querySelectorAll('input, select');
    const userevn = inputs[0].value.trim();
    const passevn = inputs[1].value.trim();
    const server = inputs[2].tagName === 'SELECT' ? inputs[2].value.trim() : (inputs[2].value.trim() || '');
    if (userevn && passevn) accounts.push({userevn, passevn, server});
  });
  const threadId = document.getElementById('threadId').value.trim();
  const type = document.getElementById('type').value.trim();
  const accountSelection = document.getElementById('accountSelection').value.trim();
  const urlzalo = document.getElementById('urlzalo').value.trim();
  const telegram_token = document.getElementById('telegram_token').value.trim();
  const telegram_chat_id = document.getElementById('telegram_chat_id').value.trim();
  const telegram_thread_id = document.getElementById('telegram_thread_id').value.trim();
  if (!gemini_api_key || !gemini_model) {
    showMsg('Vui lòng nhập đủ Gemini API Key và Model!', true); return;
  }
  if (accounts.length === 0) {
    showMsg('Phải có ít nhất 1 tài khoản!', true); return;
  }
  const config = {
    accounts_json: JSON.stringify(accounts),
    gemini_api_key,
    gemini_model,
    threadId,
    type,
    accountSelection,
    urlzalo,
    telegram_token,
    telegram_chat_id,
    telegram_thread_id
  };
  try {
    const res = await fetch(getBaseUrl() + '/config/data/options.json', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config)
    });
    if (!res.ok) throw new Error('Lưu thất bại!');
    // Gọi API khởi động lại
    const restartRes = await fetch(getBaseUrl() + '/restart', {method: 'POST'});
    if (!restartRes.ok) throw new Error('Không thể khởi động lại!');
    showMsg('Đã lưu và gửi yêu cầu khởi động lại thành công!');
  } catch (e) {
    showMsg('Lỗi khi lưu hoặc khởi động lại: ' + e.message, true);
  }
};

async function fetchLogs() {
  try {
    const res = await fetch(getBaseUrl() + '/logs');
    if (!res.ok) throw new Error('Không thể tải logs');
    const text = await res.text();
    document.getElementById('logs').textContent = text;
  } catch (e) {
    document.getElementById('logs').textContent = 'Không thể tải logs: ' + e.message;
  }
}

// Đảm bảo fetchLogs interval chỉ chạy khi modal mở, dừng khi modal đóng
let configLogsInterval = null;

// Khi mở modal thì load config và logs
function showConfigModal() {
  document.getElementById('configModal').classList.remove('hidden');
  loadConfig();
  fetchLogs();
  if (configLogsInterval) clearInterval(configLogsInterval);
  configLogsInterval = setInterval(fetchLogs, 2000);
}

// Khi đóng modal thì dừng logs interval
function closeConfigModal() {
  document.getElementById('configModal').classList.add('hidden');
  if (configLogsInterval) clearInterval(configLogsInterval);
  configLogsInterval = null;
}

// Thêm ghi chú hướng dẫn cho Gemini API Key
function insertGeminiApiKeyNote() {
  const apiKeyInput = document.getElementById('gemini_api_key');
  if (!apiKeyInput) return;
  let note = document.getElementById('gemini_api_key_note');
  if (!note) {
    note = document.createElement('div');
    note.id = 'gemini_api_key_note';
    note.style.fontSize = '0.95em';
    note.style.color = '#f59e42';
    note.style.marginTop = '2px';
    apiKeyInput.parentNode.insertBefore(note, apiKeyInput.nextSibling);
  }
  note.textContent = 'Lưu ý: Đối với server NPC và SPC, bạn phải điền đúng Gemini API Key. Các server khác có thể điền bừa.';
}

// Thêm ghi chú cho Gemini Model
function insertGeminiModelNote() {
  const modelInput = document.getElementById('gemini_model');
  if (!modelInput) return;
  let note = document.getElementById('gemini_model_note');
  if (!note) {
    note = document.createElement('div');
    note.id = 'gemini_model_note';
    note.style.fontSize = '0.95em';
    note.style.color = '#f59e42';
    note.style.marginTop = '2px';
    modelInput.parentNode.insertBefore(note, modelInput.nextSibling);
  }
  note.textContent = 'Có thể để mặc định.';
}

document.addEventListener('DOMContentLoaded', function () {
  const configModal = document.getElementById('configModal');
  const openConfigBtn = document.getElementById('configBtn');
  const closeConfigModalBtn = document.getElementById('closeConfigModal');
  if (openConfigBtn && configModal && closeConfigModalBtn) {
    openConfigBtn.addEventListener('click', showConfigModal);
    closeConfigModalBtn.addEventListener('click', closeConfigModal);
  }
  insertGeminiApiKeyNote();
  insertGeminiModelNote();
});
