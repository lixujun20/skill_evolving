\begin{array}{rl}
J(S)&=\mathbb{E}_{q\sim D, x\sim p_S(\cdot|q)}U(x)\\
&=\mathbb{E}_{q\sim D}\int p_S(x|q)U(x)dx\\
&=\mathbb{E}_{q\sim D}\int U(x)\int p_S(x,z|q)dxdz\\
&=\mathbb{E}_{q\sim D}\int U(x)\int p_S(x|z,q)p_S(z)dxdz\\
\end{array}
我在明确一下，我们的J是这样的。
* EM要解决的问题是p_S(x|q)是intractable的，因为有隐变量。我们这里也是类似的问题。p_S(x|q)直接难求，但是p_S(x|z,q)和p_S(z)都比较好得到。
* 他们由于直接优化的是p_S(x|q), 而且X已经采出来了，可以取对数。我们看起来是x还没采出来，相当于又多了一层积分。
所以，我们可以改进EM的算法嘛？比如用两层Jensen？会不会变得很不紧？
或者，这个问题在机器学习中有没有专门的研究？一般怎么解决的？