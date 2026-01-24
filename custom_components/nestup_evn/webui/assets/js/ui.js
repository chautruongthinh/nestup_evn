// Detect if running inside Home Assistant frontend (global, as early as possible)

// UI Management Module
class UIManager {
    constructor() {
        this.setupEventListeners();
        this.setupAnimations();
    }    // Setup các event listeners
    setupEventListeners() {
        // Ripple effect for all buttons
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('btn')) {
                this.createRippleEffect(e);
            }
        });

        // Theme selector
        const themeSelector = document.getElementById('themeSelect');
        if (themeSelector) {
            themeSelector.addEventListener('change', (e) => this.changeTheme(e.target.value));
            // Load saved theme
            this.loadSavedTheme();
        }
    }

    // Tạo ripple effect
    createRippleEffect(e) {
        const btn = e.target;
        const circle = document.createElement('span');
        circle.className = 'ripple';
        
        const rect = btn.getBoundingClientRect();
        circle.style.left = (e.clientX - rect.left) + 'px';
        circle.style.top = (e.clientY - rect.top) + 'px';
        circle.style.width = circle.style.height = Math.max(rect.width, rect.height) + 'px';
        
        btn.appendChild(circle);
        setTimeout(() => circle.remove(), 600);
    }    // Change theme function
    changeTheme(themeName) {
        // Remove all existing theme classes
        const themes = [
            'dark-gradient', 'cyberpunk', 'neon-dreams', 'aurora-borealis', 
            'synthwave', 'glassmorphism', 'neubrutalism', 'matrix-rain', 
            'sunset-vibes', 'ocean-depth', 'midnight-purple', 'golden-hour', 
            'forest-mist', 'cosmic-dust', 'tokyo-night', 'minimal-light'
        ];
        themes.forEach(theme => {
            document.body.removeAttribute('data-theme');
        });
        // Apply new theme
        document.body.setAttribute('data-theme', themeName);
        // Save theme preference (safe)
        if (!window.__DISABLE_THEME_PERSISTENCE__ && this.isLocalStorageAvailable()) {
            try {
                localStorage.setItem('uiTheme', themeName);
            } catch (e) {
                console.warn('Could not save theme to localStorage:', e);
                window.__DISABLE_THEME_PERSISTENCE__ = true;
            }
        }
        // Update theme selector value
        const themeSelector = document.getElementById('themeSelect');
        if (themeSelector) {
            themeSelector.value = themeName;
        }
        // Apply theme to form elements
        this.applyThemeToFormElements(themeName);
        // Trigger chart updates
        if (window.chartManager) {
            window.chartManager.updateChartsTheme();
        }
        
    }
      // Apply theme to form elements and containers
    applyThemeToFormElements(themeName) {
        // Apply to form elements
        const formElements = document.querySelectorAll('select, input[type="date"]');
        formElements.forEach(element => {
            // Remove any existing theme classes
            element.className = element.className.replace(/theme-\w+/g, '');
            // Add new theme class if needed (handled by CSS data-theme attribute)
        });
        
        // Apply to search results container
        const searchResultsContainer = document.getElementById('searchResult');
        if (searchResultsContainer) {
            searchResultsContainer.style.transition = 'background-color 0.5s, border-color 0.5s, box-shadow 0.5s';
        }
    }
    
    // Load saved theme
    loadSavedTheme() {
        let savedTheme = 'dark-gradient';
        const validThemes = [
            'dark-gradient', 'cyberpunk', 'neon-dreams', 'aurora-borealis',
            'synthwave', 'glassmorphism', 'neubrutalism', 'matrix-rain',
            'sunset-vibes', 'ocean-depth', 'midnight-purple', 'golden-hour',
            'forest-mist', 'cosmic-dust', 'tokyo-night', 'minimal-light'
        ];
        if (!window.__DISABLE_THEME_PERSISTENCE__ && this.isLocalStorageAvailable()) {
            try {
                const theme = localStorage.getItem('uiTheme');
                if (theme && typeof theme === 'string' && validThemes.includes(theme)) {
                    savedTheme = theme;
                } else {
                    localStorage.removeItem('uiTheme');
                }
            } catch (e) {
                console.warn('Could not read theme from localStorage:', e);
                window.__DISABLE_THEME_PERSISTENCE__ = true;
            }
        }
        this.changeTheme(savedTheme);
        // Đảm bảo luôn lưu lại key uiTheme nếu chưa có (kể cả lần đầu vào trang)
        if (!window.__DISABLE_THEME_PERSISTENCE__ && this.isLocalStorageAvailable()) {
            try {
                localStorage.setItem('uiTheme', savedTheme);
            } catch (e) {
                // Không làm gì nếu localStorage không truy cập được
            }
        }
    }
    
    // Get theme display name
    getThemeDisplayName(themeName) {
        const themeNames = {
            'dark-gradient': 'Dark Gradient',
            'cyberpunk': 'Cyberpunk 2025',
            'neon-dreams': 'Neon Dreams',
            'aurora-borealis': 'Aurora Borealis',
            'synthwave': 'Synthwave',
            'glassmorphism': 'Glassmorphism',
            'neubrutalism': 'Neubrutalism',
            'matrix-rain': 'Matrix Rain',
            'sunset-vibes': 'Sunset Vibes',
            'ocean-depth': 'Ocean Depth',
            'midnight-purple': 'Midnight Purple',
            'golden-hour': 'Golden Hour',
            'forest-mist': 'Forest Mist',
            'cosmic-dust': 'Cosmic Dust',
            'tokyo-night': 'Tokyo Night',
            'minimal-light': 'Minimal Light'
        };
        return themeNames[themeName] || themeName;
    }

    // Render summary container    // Render summary container - Old design style
    renderSummaryContainer(trendData) {
        const summaryContainer = document.getElementById('summaryContainer');
        summaryContainer.innerHTML = '';
        
        trendData.forEach((data, index) => {
            const summaryDiv = document.createElement('div');
            summaryDiv.className = 'summary-month-card';
            summaryDiv.id = `summary-month-${index}`;
            
            // Determine trend color and symbol
            let trendSymbol = '—';
            let trendClass = 'neutral';
            if (data.trend === 'up') {
                trendSymbol = '▲';
                trendClass = 'positive';
            } else if (data.trend === 'down') {
                trendSymbol = '▼';
                trendClass = 'negative';
            }            summaryDiv.innerHTML = `
                <h4>${data.isCurrentPeriod ? 'Kỳ này' : `Tháng ${data.monthNum.toString().padStart(2, '0')}`}</h4>
                <div class="summary-stat-inline">
                    <i class="fas fa-bolt text-yellow-400"></i>
                    <span>Tổng:</span>
                    <strong>${data.totalConsumption.toFixed(1)}</strong>
                    <span>kWh</span>
                </div>
                <div class="summary-stat-inline">
                    <i class="fas fa-coins text-green-400"></i>
                    <span>Tiền:</span>
					<strong>${data.monthlyCost.toLocaleString()}</strong>
					<span>VND${data.isCurrentPeriod ? ' (tạm tính)' : ''}</span>
                </div>                <div class="summary-stat-row">
                    <span class="min-value">
                        Min: <i class="fas fa-arrow-down text-blue-500"></i><strong class="text-blue-500">${data.min.toFixed(1)}</strong>
                    </span>
                    <span class="max-value">
                        Max: <i class="fas fa-arrow-up text-red-500"></i><strong class="text-red-500">${data.max.toFixed(1)}</strong>
                    </span>
                    <span>Avg: <strong>${data.avg.toFixed(1)}</strong></span>
                </div>
                <div class="summary-change ${trendClass}">
                    ${trendSymbol} ${data.trendValue > 0 ? '+' : ''}${data.trendValue.toFixed(1)} (${data.trendPercent > 0 ? '+' : ''}${data.trendPercent.toFixed(1)}%)
                </div>
            `;
            summaryContainer.appendChild(summaryDiv);
        });
    }

    // Update summary numbers with animation
	updateSummaryNumbers(summary) {
		const totalCostEl = document.getElementById('totalCost');
		const avgMonthlyCostEl = document.getElementById('avgMonthlyCost');
		const avgMonthlyConsumptionEl = document.getElementById('avgMonthlyConsumption');
		const avgDailyConsumptionEl = document.getElementById('avgDailyConsumption');

		if (!totalCostEl) return;

		totalCostEl.innerHTML = '';

		const costValueSpan = document.createElement('span');
		costValueSpan.className = 'summary-cost-value';
		totalCostEl.appendChild(costValueSpan);

		this.animateCounterUp(costValueSpan, summary.totalCost, 0);

		if (summary.estimated) {
			const badge = document.createElement('span');
			badge.className = 'ml-2 text-xs text-yellow-400';
			badge.textContent = '(tạm tính)';
			totalCostEl.appendChild(badge);
		}

		this.animateCounterUp(avgMonthlyCostEl, summary.avgMonthlyCost, 0);
		this.animateCounterUp(avgMonthlyConsumptionEl, summary.avgMonthlyConsumption, 2);
		this.animateCounterUp(avgDailyConsumptionEl, summary.avgDailyConsumption, 2);
	}

    // Counter up animation
    animateCounterUp(element, value, decimals = 0) {
        if (!element) return;
        
        const duration = 900;
        const start = parseFloat(element.textContent.replace(/,/g, '')) || 0;
        const end = value;
        const startTime = performance.now();
        
        if (start === end) return;
        
        const animate = (now) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const current = start + (end - start) * progress;
            
            if (decimals > 0) {
                element.textContent = current.toLocaleString(undefined, { maximumFractionDigits: decimals });
            } else {
                element.textContent = Math.round(current).toLocaleString();
            }
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                element.textContent = decimals > 0 ? 
                    end.toLocaleString(undefined, { maximumFractionDigits: decimals }) : 
                    Math.round(end).toLocaleString();
                element.classList.add('changed');
                setTimeout(() => element.classList.remove('changed'), 700);
            }
        };
        
        requestAnimationFrame(animate);
    }

    // Populate month select
    populateMonthSelect(uniqueMonths) {
        const monthSelect = document.getElementById('monthSelect');
        monthSelect.innerHTML = '';
        
        uniqueMonths.forEach(monthYear => {
            const option = document.createElement('option');
            option.value = monthYear;
            option.textContent = `Tháng ${monthYear}`;
            monthSelect.appendChild(option);
        });
        
        if (uniqueMonths.length > 0) {
            monthSelect.value = uniqueMonths[0];
        }
    }

    // Populate account select
    populateAccountSelect(accounts) {
        const accountSelect = document.getElementById('accountSelect');
        accountSelect.innerHTML = '';
        
        accounts.forEach((account, index) => {
            const option = document.createElement('option');
            option.value = account.userevn;
            option.textContent = account.userevn;
            accountSelect.appendChild(option);
            if (index === 0) option.selected = true;
        });
    }

    // Update account avatar
    updateAccountAvatar(account) {
        const avatar = document.getElementById('accountAvatar');
        if (!avatar) return;
        
        if (!account) {
            avatar.innerHTML = '<i class="fas fa-user"></i>';
            return;
        }
        
        // Lấy ký tự đầu hoặc số cuối tài khoản làm avatar
        let display = account[0];
        if (/\d/.test(account[account.length-1])) {
            display = account[account.length-1];
        }
        avatar.textContent = display;
    }    // Render search results - simplified as we now use the modal dialog directly
    renderSearchResults(filteredData, summary = null, showSummary = false) {
        // This function is kept for compatibility, but we now show results directly in the modal
        // The search results container is no longer used
    }// Khởi tạo ô kết quả tìm kiếm - not needed anymore as we now use the detail modal directly
    initializeSearchResults() {
        // This function is kept for compatibility but no longer needs to do anything
        // since we're now showing results directly in the modal popup
    }

    // Clear all data displays
    clearData() {
        const elements = ['totalCost', 'avgMonthlyCost', 'avgMonthlyConsumption', 'avgDailyConsumption'];
        elements.forEach(id => {
            const element = document.getElementById(id);
            if (element) element.textContent = '';
        });
        
        const monthSelect = document.getElementById('monthSelect');
        if (monthSelect) monthSelect.innerHTML = '';
        
        const searchResult = document.getElementById('searchResult');
        if (searchResult) searchResult.innerHTML = '';
        
        const summaryContainer = document.getElementById('summaryContainer');
        if (summaryContainer) summaryContainer.innerHTML = '';
    }

    // Show/hide loader
    showLoader(show = true) {
        const loader = document.getElementById('mainLoader');
        if (loader) {
            loader.style.display = show ? 'block' : 'none';
        }
    }

    // Setup SVG background animation
    setupAnimations() {
        this.animateSVGBackground();
    }

    // Animate SVG Background
    animateSVGBackground() {
        const c1 = document.getElementById('bg-c1');
        const c2 = document.getElementById('bg-c2');
        const e1 = document.getElementById('bg-e1');
        
        if (!c1 || !c2 || !e1) return;
        
        let t = 0;
        const loop = () => {
            t += 0.008;
            c1.setAttribute('cx', 400 + Math.sin(t) * 60);
            c1.setAttribute('cy', 300 + Math.cos(t/2) * 40);
            c2.setAttribute('cx', 1600 + Math.cos(t/1.5) * 80);
            c2.setAttribute('cy', 800 + Math.sin(t/1.2) * 60);
            e1.setAttribute('rx', 120 + Math.sin(t/1.3) * 18);
            e1.setAttribute('ry', 60 + Math.cos(t/1.7) * 10);
            requestAnimationFrame(loop);
        };
        loop();
    }

    // Cập nhật hiển thị thông tin chu kỳ thanh toán
    updateBillingCycleDisplay(billingInfo) {
        const billingCycleInfo = document.querySelector('.billing-cycle-info span');
        if (billingCycleInfo) {
            billingCycleInfo.textContent = billingInfo.description;
        }
    }

    // Hiển thị modal cấu hình chu kỳ thanh toán
    showBillingCycleConfig(currentCycle, onSave) {
        console.log('Creating billing cycle modal...');
          const modal = document.createElement('div');
        modal.className = 'billing-cycle-modal';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3><i class="fas fa-calendar-alt"></i> Cấu hình chu kỳ thanh toán</h3>
                    <button class="close-btn" onclick="this.closest('.billing-cycle-modal').remove()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="modal-body">
                    <div class="billing-cycle-info">
                        <p><strong>Hiện tại:</strong> ${currentCycle.description}</p>
                    </div>
                    
                    <div class="billing-cycle-options">
                        <label class="cycle-option">
                            <input type="radio" name="cycleType" value="calendar" ${currentCycle.type === 'calendar' ? 'checked' : ''}>
                            <div class="option-content">
                                <h4>Theo tháng dương lịch</h4>
                                <p>Từ đầu tháng (ngày 1) đến cuối tháng</p>
                            </div>
                        </label>
                        
                        <label class="cycle-option">
                            <input type="radio" name="cycleType" value="custom" ${currentCycle.type !== 'calendar' ? 'checked' : ''}>
                            <div class="option-content">
                                <h4>Theo chu kỳ thanh toán</h4>
                                <p>Từ ngày cố định hàng tháng</p>
                                <div class="start-day-input">
                                    <label for="startDay">Ngày bắt đầu chu kỳ:</label>
                                    <select id="startDay" ${currentCycle.type === 'calendar' ? 'disabled' : ''}>
                                        ${Array.from({length: 28}, (_, i) => i + 1).map(day => 
                                            `<option value="${day}" ${currentCycle.startDay === day ? 'selected' : ''}>${day}</option>`
                                        ).join('')}
                                    </select>
                                </div>
                            </div>
                        </label>
                    </div>
                    
                    <div class="billing-example">
                        <h4>Ví dụ:</h4>
                        <div id="exampleText">
                            ${this.getBillingExampleText(currentCycle.type === 'calendar' ? 'calendar' : 'custom', currentCycle.startDay || 15)}
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="this.closest('.billing-cycle-modal').remove()">Hủy</button>
                    <button class="btn btn-primary" id="saveBillingCycle">Lưu cấu hình</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        console.log('Modal appended to body, modal visible?', modal.style.display !== 'none');
        
        // Event listeners cho modal
        const cycleTypeInputs = modal.querySelectorAll('input[name="cycleType"]');
        const startDaySelect = modal.querySelector('#startDay');
        const exampleText = modal.querySelector('#exampleText');
        
        cycleTypeInputs.forEach(input => {
            input.addEventListener('change', (e) => {
                const isCustom = e.target.value === 'custom';
                startDaySelect.disabled = !isCustom;
                exampleText.innerHTML = this.getBillingExampleText(e.target.value, parseInt(startDaySelect.value));
            });
        });
        
        startDaySelect.addEventListener('change', (e) => {
            const cycleType = modal.querySelector('input[name="cycleType"]:checked').value;
            exampleText.innerHTML = this.getBillingExampleText(cycleType, parseInt(e.target.value));
        });
          // Close modal when clicking outside
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                console.log('Closing modal - clicked outside');
                modal.remove();
            }
        });
        
        // Close modal with ESC key
        const handleEscKey = (e) => {
            if (e.key === 'Escape') {
                console.log('Closing modal - ESC key pressed');
                modal.remove();
                document.removeEventListener('keydown', handleEscKey);
            }
        };
        document.addEventListener('keydown', handleEscKey);
        
        // Save button
        modal.querySelector('#saveBillingCycle').addEventListener('click', () => {
            const cycleType = modal.querySelector('input[name="cycleType"]:checked').value;
            const startDay = parseInt(startDaySelect.value);
            
            console.log('Saving billing cycle:', { cycleType, startDay });
            
            onSave({
                type: cycleType === 'calendar' ? 'calendar' : 'cycle',
                startDay: cycleType === 'calendar' ? 1 : startDay
            });
            
            modal.remove();
            document.removeEventListener('keydown', handleEscKey);
        });
        
        console.log('Modal setup complete');
    }
    
    // Tạo text ví dụ cho chu kỳ thanh toán
    getBillingExampleText(type, startDay) {
        const today = new Date();
        const currentMonth = today.getMonth() + 1;
        const currentYear = today.getFullYear();
        
        if (type === 'calendar') {
            return `
                <div class="example-item">
                    <strong>Tháng ${currentMonth}/${currentYear}:</strong> 01/${currentMonth}/${currentYear} - ${new Date(currentYear, currentMonth, 0).getDate()}/${currentMonth}/${currentYear}
                </div>
            `;        } else {
            const startDate = new Date(currentYear, currentMonth - 1, startDay);
            const endDate = new Date(currentYear, currentMonth, startDay - 1);
            
            return `
                <div class="example-item">
                    <strong>Chu kỳ tháng ${currentMonth}/${currentYear}:</strong> 
                    ${startDate.getDate().toString().padStart(2, '0')}/${(startDate.getMonth() + 1).toString().padStart(2, '0')}/${startDate.getFullYear()} - 
                    ${endDate.getDate().toString().padStart(2, '0')}/${(endDate.getMonth() + 1).toString().padStart(2, '0')}/${endDate.getFullYear()}
                </div>
                <div class="example-note">
                    <i class="fas fa-info-circle"></i> Chu kỳ thanh toán từ ngày ${startDay} tháng hiện tại đến ngày ${startDay - 1} tháng tiếp theo
                </div>
            `;}
    }
    
    // Hiển thị toast notification
    showToast(message, type = 'success') {
        // Remove existing toast if any
        const existingToast = document.querySelector('.toast-notification');
        if (existingToast) {
            existingToast.remove();
        }
        
        const toast = document.createElement('div');
        toast.className = `toast-notification toast-${type}`;
        toast.innerHTML = `
            <div class="toast-content">
                <i class="fas ${type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'}"></i>
                <span>${message}</span>
            </div>
        `;
        
        document.body.appendChild(toast);
        
        // Auto remove after 3 seconds
        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    // Hiển thị dữ liệu 5 ngày gần đây trong card tìm kiếm
    displayRecentDays(recentData) {
        const recentDaysContainer = document.getElementById('recentDaysData');
        if (!recentDaysContainer) return;

        recentDaysContainer.innerHTML = '';

        if (!recentData || recentData.length === 0) {
            const emptyMessage = document.createElement('div');
            emptyMessage.className = 'text-sm text-gray-400 text-center py-2';
            emptyMessage.textContent = 'Không có dữ liệu gần đây';
            recentDaysContainer.appendChild(emptyMessage);
            return;
        }

        // Hiển thị mỗi ngày trong danh sách
        recentData.forEach(day => {
            const consumption = day["Điện tiêu thụ (kWh)"];
            
            // Format date nicely
            const date = new Date(day.Ngày.split('-').reverse().join('-'));
            const formattedDate = date.toLocaleDateString('vi-VN', {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric'
            });
            
            // Xác định class dựa trên mức tiêu thụ
            let consumptionClass, icon;
            if (consumption > 10) {
                consumptionClass = 'consumption-high';
                icon = '🔥';
            } else if (consumption > 5) {
                consumptionClass = 'consumption-medium';
                icon = '⚡';
            } else if (consumption > 0) {
                consumptionClass = 'consumption-low';
                icon = '💡';
            } else {
                consumptionClass = 'consumption-zero';
                icon = '🕯️';
            }
            
            const dayElement = document.createElement('div');
            dayElement.className = 'recent-day-item';
            dayElement.innerHTML = `
                <span class="date"><span class="icon">${icon}</span>${formattedDate}</span>
                <span class="consumption ${consumptionClass}">${consumption.toFixed(2)} kWh</span>
            `;
            
            recentDaysContainer.appendChild(dayElement);
        });
    }
    
    // Kiểm tra localStorage có khả dụng không
    isLocalStorageAvailable() {
        try {
            const testKey = '__test__';
            localStorage.setItem(testKey, '1');
            localStorage.removeItem(testKey);
            return true;
        } catch (e) {
            return false;
        }
    }

    // Detect if running inside Home Assistant frontend
    isHomeAssistantEnv() {
        try {
            // Heuristic: running in iframe and URL contains 'lovelace' or 'home-assistant' or 'hass'
            const inIframe = window.parent && window.parent !== window;
            const url = window.location.href;
            return (
                inIframe &&
                (/lovelace|home-assistant|hass/i.test(url) || window.parent.hass !== undefined)
            );
        } catch (e) {
            return false;
        }
    }
}

// Export cho sử dụng global
window.UIManager = UIManager;
