$i = 2101

#Write-Host "正在处理页码: $i ..."
#ass scrape-instruments --sql-uri postgresql://louis@fre.local/louis --product-type BOND --start-page-number $i
#i -= 100

for (; $i -gt 0; $i -= 100) {
  $endPage = $i + 99
    
  Write-Host "正在处理页码: $i 到 $endPage ..."
    
  # 执行命令
  ass scrape-instruments `
    --sql-uri "postgresql://louis@fre.local/louis" `
    --product-type BOND `
    --start-page-number $i `
    --end-page-number $endPage
      
  Write-Host "----------------------------------------"
  Start-Sleep 60
}