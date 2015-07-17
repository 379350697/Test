/**
 * 
 */

/**
 * ********************************************************* 
 *@Description: 
 * 
 * @author: wl 
 * @date: 2015-7-17
 * @company: Rongbo
 *
 * ********************************************************* 
 */
public class TestSort {
	
	int []a = {5,7,2,1,9,4,6,5};
	
	//冒泡排序
	public void bubbleSort(){
		int count = 0;
		for(int i =0;i<a.length;i++){
			for(int j=0;j<a.length-i-1;j++){
				int temp=0;
				if(a[j]<a[j+1]){
					temp = a[j];
					a[j] = a[j+1];
					a[j+1] = temp;
					count ++;
				}
			}
		}
		System.out.println("冒泡排序: ------------------------");
		System.out.println("排序个数:"+a.length);
		System.out.println("排序次数"+count);
		for(int i=0;i<a.length;i++){
			System.out.print(a[i]+"    ");
		}
	}
	
	//快速排序
	public void quickSort(int left,int right){
		int count = 0;
		int i,j,t,temp;
		i = left;
		j = right;
		temp = left;
		if(left>right){
			System.out.println("快速排序: ------------------------");
			System.out.println("排序个数:"+a.length);
			System.out.println("排序次数"+count);
			for(int n=0;n<a.length;n++){
				System.out.print(a[n]+"    ");
			}
			return;
		}
		while(i!=j){
			while(a[j]>temp&&i<j){
				j--;
			}
			while(a[i]<temp&&i<j){
				i++;
			}
			if(i<j){
				t = a[i];
				a[i] = a[j];
				a[j] = t;
			}
		}
		//最终将基准数归位
		a[left] = a[i];
		a[i] = temp;
		quickSort(left, i-1);
		quickSort(i+1, right);
	}

	/**
	 * @param args
	 */
	public static void main(String[] args) {
		TestSort ts = new TestSort();
		ts.bubbleSort();
		ts.quickSort(0, ts.a.length-1);
	}

}
